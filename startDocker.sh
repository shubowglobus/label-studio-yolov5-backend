docker run -it \
  -v ${PWD}/weights:/app/weights \
  -v /mnt/eds_data/label_studio_files:/app/local_storage \
  -v /mnt/eds_data/label_studio:/app/data_store \
  --env-file .env \
  -p 8000:8000 \
  -p 9090:9090 \
  ls-backend-yolo:latest