#!/usr/bin/env python
# coding: utf-8

import json
import os
import re
from pathlib import Path
import statistics
import tempfile
from typing import Optional, Union

import requests
import torch
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from minio import Minio
from pydantic import BaseModel
from label_studio_tools.core.utils.params import get_env

# ----------------------------------------------------------------------------

app = FastAPI()

# ----------------------------------------------------------------------------


class Task(BaseModel):
    task: dict
    project: Optional[int] = None


def load_model(model_weights: str, model_version: str):
    model = torch.hub.load('ultralytics/yolov5', 'custom', path=model_weights)
    return {'model': model, 'model_version': model_version}


def _yolo_to_ls(model, x: float, y: float, width: float, height: float,
                score: float, n: int) -> tuple:
    x = (x - width / 2) * 100
    y = (y - height / 2) * 100
    w = width * 100
    h = height * 100
    x, y, w, h, score = [float(i) for i in [x, y, w, h, score]]
    try:
        label = model.names[int(n)]
    except ValueError:
        label = n
    return x, y, w, h, round(score, 2), label


def _pred_dict(model_version: str, x: float, y: float, w: float, h: float,
               score: float, label: str, from_name='label') -> dict:
    return {
        'type': 'rectanglelabels',
        'score': score,
        'value': {
            'x': x,
            'y': y,
            'width': w,
            'height': h,
            'rectanglelabels': [label]
        },
        'to_name': 'image',
        'from_name': from_name,
        'model_version': model_version
    }


@app.post('/predict')
def predict_endpoint(task: Task) -> JSONResponse:
    print(task)
    _task = task.task
    if not _task.get('project'):
        if task.project:
            if task.project not in MODELS.keys():
                raise HTTPException(
                    404, f'Project id `{task.project}` does not exist!')
            _task['project'] = task.project
        else:
            raise HTTPException(
                404, 'Parameter `project` is required when the task does not '
                'contain a project id number!')
    task = _task

    image_url = task['data']['image']
    if image_url.startswith('/data/'):
        if image_url.startswith('/data/local-files'):
            _root = "/app/local_storage"
            _, img_path = image_url.split('/data/', 1)[-1].split('?d=')
            img_path = os.path.join(_root, img_path)
        else:
            _root = "/app/data_store"
            _, img_path = image_url.split('/data/', 1)[-1].split('?d=')
            img_path = os.path.join(_root, img_path)
    else:
        img = Path(image_url)

        with tempfile.NamedTemporaryFile() as f:
            if image_url.startswith('http'):
                r = requests.get(image_url)
                if r.status_code == 200:
                    f.write(r.content)
                else:
                    return JSONResponse(content=r.text, status_code=404)
            else:
                image_data = s3.get_object(img.parent.name, img.name)
                f.write(image_data.read())
            f.seek(0)
            img_path = f.name
    
    model_dict = MODELS[task['project']]
    scores = []
    results = []
    
    for from_name, model_dict in model_dict.items():
        model_version = model_dict['model_version']
        model = model_dict['model']
        model_preds = model(img_path)

        pred_xywhn = model_preds.xywhn[0]

        for pred in pred_xywhn:
            _result = _yolo_to_ls(model, *pred)
            result = _pred_dict(model_version, *_result, from_name)
            scores.append(result['score'])
            results.append(result)

    if not results:
        results.append({
            'type': 'choices',
            'value': {
                'choices': [os.environ['LABEL_STUDIO_BG_LABEL']]
            },
            'to_name': 'is_excluded',
            'from_name': 'is_excluded'
        })

    pred = {'result': results}
    if scores:
        pred['score'] = statistics.mean(scores)
        
    print(pred)

    return JSONResponse(status_code=200, content=pred)


# ----------------------------------------------------------------------------

if __name__ == '__main__':
    load_dotenv()

    with open('weights/models_config.json') as j:
        models_config = json.load(j)

    MODELS = {}
    for m in models_config:
        for p in m['projects']:
            if p['project_id'] in MODELS.keys():
                MODELS[p['project_id']].update(
                    {p["from_name"]: load_model(m['weights'], m['model_version'])}
                )
            else:
                MODELS.update(
                    {
                        p['project_id']: 
                        {p["from_name"]: load_model(m['weights'], m['model_version'])}
                    }
                )

    # s3_endpoint = re.sub(r'https?://', '', os.environ['S3_ENDPOINT'])
    # s3 = Minio(s3_endpoint,
    #            access_key=os.environ['S3_ACCESS_KEY'],
    #            secret_key=os.environ['S3_SECRET_KEY'])

    uvicorn.run(app, host='0.0.0.0', port=8000)  # noqa
