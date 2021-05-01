FROM python:3.8.8

RUN apt-get update
RUN apt-get install apt-utils make automake gcc g++ subversion python3-dev libffi-dev libssl-dev -y
RUN apt-get install ffmpeg libsm6 libxext6 certbot  -y
RUN pip3 install --upgrade pip setuptools wheel
RUN pip3 install numpy aiohttp aiortc av opencv-python pillow aioprocessing zmq
