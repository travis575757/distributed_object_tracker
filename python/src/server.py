import argparse
import asyncio
import sys
import json
import logging
import os
import ssl
import uuid
import time
import random
import socket
import queue

import cv2
import numpy as np
import zmq
from zmq.utils.monitor import recv_monitor_message
from PIL import Image
import io
import base64
from aiohttp import web
from av import VideoFrame

from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder, MediaRelay
from aioprocessing import AioProcess, AioQueue
import multiprocessing
from multiprocessing import Value, Manager
import threading

ROOT = os.path.dirname(__file__)

logger = logging.getLogger("pc")
pcs = set()
relay = MediaRelay()

# constants
TRACKING_UPDATE_INTERVAL = 3
HEARTBEAT_INTERVAL = 0.25
HEARTBEAT_TIMEOUT = 1

# workers
manager = Manager()
used_workers = manager.dict()
workers = manager.dict()

process_in_queue = AioQueue()
img_out_queue = AioQueue()

def pull_process(process_in_queue, img_out_queue):
    # setup sockets
    worker_ip = "tcp://*:5557"
    pub_ip = "tcp://*:5556"

    context = zmq.Context()

    # pub socket
    pub_socket = context.socket(zmq.PUB)
    pub_socket.bind(pub_ip)
    # pull socket
    poller = zmq.Poller()
    rep_socket = context.socket(zmq.PULL)
    rep_socket.bind(worker_ip)
    poller.register(rep_socket, zmq.POLLIN)

    img_cache = {}  # used for for cacheing images while tracking
    track_mode = 0  # 0 = none, 1 =  multi, 2 = aggregate
    heartbeat_timer = 0 # timer used for triggering worker pinging and timeouts

    last_reinit = 0  # timer for tracking reinitalization of worker locations
    worker_colors = [tuple(
            255*np.array(
                list('{0:03b}'.format(i))
            ).astype(np.int32)
        ) for i in range(8)]  # list of possible worker colors, 8 given as permutatoins of rgb values

    # incomming zmq message processing
    while True:

        # update heartbeat
        t0 = time.time()
        if t0 - heartbeat_timer > HEARTBEAT_INTERVAL:
            for worker, info in workers.items():
                # send ping
                ping_msg = {"type": "PING"}
                pub_socket.send_multipart(
                    (worker, bytes(json.dumps(ping_msg), 'utf-8')))
                # remove timedout workers
                if t0 - info['last_seen'] > HEARTBEAT_TIMEOUT:
                    print('worker timed out: {}'.format(worker))
                    sys.stdout.flush()
                    worker_colors.append(workers[worker]['color'])
                    del workers[worker]
                    if worker in used_workers:
                        del used_workers[worker]
                    print('current workers: {}'.format(workers.keys()))
                    sys.stdout.flush()
            heartbeat_timer = t0

        # process incomming process messages
        while not process_in_queue.empty():
            try:
                msg = process_in_queue.get(block=True, timeout=0.01)

                if msg['type'] == "FRAME":
                    # frame type, send frame to all workers via frame_ channel and stored in img_cache
                    img = msg['data']
                    t_send = time.time()
                    pub_socket.send_multipart((b"frame_",
                                               bytes(json.dumps({
                                                   "type": "FRAME",
                                                   "dtype": str(img.dtype),
                                                   "shape": img.shape,
                                                   "time": t_send
                                               }), 'utf-8'),
                                               img))
                    img_cache[t_send] = {"img": img, "bboxs": {}}
                elif msg['type'] == "WORKER_SUPPORT":
                    # configured session workers and send supports to appropriate workers
                    used_workers.clear()
                    for worker, data in zip(workers.keys(), msg['data']):
                        message = {"type": "SUPPORT", "data": data}
                        pub_socket.send_multipart(
                            (worker, bytes(json.dumps(message), 'utf-8')))
                        used_workers[worker] = workers[worker]
                elif msg['type'] == "MODE":
                    # set the current mode
                    track_mode = msg['mode']
                elif msg['type'] == "RESET":
                    # clear image cache and reset mode
                    img_cache = {}
                    track_mode = 0
                else:
                    print("Invalid message type received on pull process: {}"
                          .format(msg['type']))
            except queue.Empty:
                pass
            except Exception as err:
                print('error: {}'.format(err))
                sys.stdout.flush()

        socks = dict(poller.poll(10))

        # process incomming zmq messages
        if socks:
            for sock, mask in socks.items():
                if mask == zmq.POLLIN:
                    message = sock.recv_json()

                    if message['type'] == "TRACK":

                        # guard against old frames, could be caused new user connections
                        if message['time'] in img_cache and bytes(message['id'], 'utf-8') in used_workers:
                            img_cache[message['time']]['bboxs'][message['id']] = {
                                "bbox": message['bbox'], "score": message['score']}
                            # if all worker predictions received
                            if len(img_cache[message['time']]['bboxs']) == len(used_workers):

                                img_record = img_cache[message['time']]['bboxs']
                                img = img_cache[message['time']]['img']

                                selected_outputs = {}

                                if track_mode == 1:
                                    selected_outputs = img_record
                                elif track_mode == 2:
                                    pred_top = None
                                    score_top = None
                                    for worker, pred in img_record.items():
                                        if pred_top is None or pred['score'] > score_top:
                                            pred_top = (worker, pred)
                                            score_top = pred['score']
                                    worker, pred = pred_top
                                    selected_outputs = {worker: pred}

                                if track_mode != 'none':
                                    for worker, pred in selected_outputs.items():
                                        bbox = pred['bbox']
                                        score = pred['score']
                                        if score > 0.6:

                                            worker_color = tuple([int(c) for c in used_workers[bytes(worker, 'utf-8')]['color']])
                                            cv2.rectangle(img, (bbox[0], bbox[1]),
                                                          (bbox[0]+bbox[2],
                                                           bbox[1]+bbox[3]),
                                                          worker_color, 3)
                                            label = "{} : {:0.3f}".format(worker[:4], score)
                                            label_size, _ = cv2.getTextSize(
                                                label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
                                            cv2.rectangle(img, (bbox[0], bbox[1] + bbox[3] - label_size[1]),
                                                          (bbox[0] + label_size[0], bbox[1] + bbox[3]), (255, 255, 255), -1)
                                            cv2.putText(
                                                    img, label, (bbox[0], bbox[1] + bbox[3]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
                                            # update detection positions if applicable
                                            if (time.time() - last_reinit) > TRACKING_UPDATE_INTERVAL and track_mode == 2:
                                                last_reinit = time.time()
                                                loc_msg = {
                                                    "type": "LOCATION",
                                                    "data": [bbox[0] + 0.5 * bbox[2], bbox[1] + 0.5 * bbox[3]]
                                                }
                                                pub_socket.send_multipart(
                                                    (b"frame_", bytes(json.dumps(loc_msg), 'utf-8')))

                                img_out_queue.put(img)
                                del img_cache[message['time']]
                    elif message['type'] == "REGISTER":
                        # worker is being registered
                        worker_id = bytes(message['id'], 'utf-8')
                        if worker_id not in workers:
                            worker_record = manager.dict()
                            worker_color = random.choice(worker_colors)
                            worker_record['color'] = manager.list(worker_color)
                            worker_record['last_seen'] = time.time()
                            worker_colors.remove(worker_color)
                            workers[worker_id] = worker_record
                            print('worker connect: {} : {}'.format(
                                message['id'], worker_record))
                            sys.stdout.flush()
                    elif message['type'] == "FIN":
                        # worker is disconnecting
                        worker_id = bytes(message['id'], 'utf-8')
                        if worker_id in workers:
                            print('worker disconnect: {} : {}'.format(
                                message['id'], workers[worker_id]))
                            sys.stdout.flush()
                            worker_colors.append(workers[bytes(message['id'], 'utf-8')]['color'])
                            worker_id = bytes(message['id'], 'utf-8')
                            del workers[worker_id]
                            if worker_id in used_workers:
                                del used_workers[worker_id]
                        fin_msg = {"type": "FIN"}
                        pub_socket.send_multipart(
                            (bytes(message['id'], 'utf-8'), bytes(json.dumps(fin_msg), 'utf-8')))
                    elif message['type'] == "PONG":
                        # worker is responding to a pong
                        if bytes(message['id'], 'utf-8') in workers:
                            workers[bytes(message['id'], 'utf-8')]['last_seen'] = time.time()
                    else:
                        print("Invalid client message received")
                        sys.stdout.flush()


pull_proc = AioProcess(target=pull_process, args=(process_in_queue, img_out_queue))
pull_proc.start()


class ObjectTrackerTrack(MediaStreamTrack):
    """
    Tracking video stream
    """

    kind = "video"

    def __init__(self, track):
        super().__init__()  # don't forget this!
        self.track = track

        self.last_frame = None

    async def recv(self):

        frame = await self.track.recv()
        img = frame.to_ndarray(format="bgr24")
        await process_in_queue.coro_put({"type": "FRAME", "data": img})

        if img_out_queue.empty():
            if self.last_frame is not None:
                return self.last_frame
            else:
                self.last_frame = frame
                return frame
        else:
            while True:
                img = await img_out_queue.coro_get()
                # drop frames if the queue is too far behind
                if img_out_queue.qsize() < 5:
                    break
            # rebuild a VideoFrame, preserving timing information
            new_frame = VideoFrame.from_ndarray(img, format="bgr24")
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base
            self.last_frame = new_frame

            return self.last_frame


async def index(request):
    index = None
    if len(pcs) > 0:
        index = "full.html"
    else:
        index = "index.html"
    content = open(os.path.join(ROOT, index), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    if len(pcs) > 0:
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"error": "Someone is already using the tracker!\nCome back another time."}
            ),
        )

    await process_in_queue.coro_put({"type": "RESET"})

    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pc_id = "PeerConnection(%s)" % uuid.uuid4()
    pcs.add(pc)

    def log_info(msg, *args):
        logger.info(pc_id + " " + msg, *args)

    log_info("Created for %s", request.remote)

    # called when the remote peer creates the data channel
    @ pc.on("datachannel")
    async def on_datachannel(channel):

        def send_workers():
            msg = {"type": "SUPPORT", "workers": []}
            for worker, data in workers.items():
                color = tuple([int(c) for c in data['color']])
                msg["workers"].append({"id": worker.decode('utf-8'), "color": color})
            channel.send(json.dumps(msg))

        send_workers()
        log_info("sending support info, num supports: {}".format(
            len(workers)))

        # storage for fragments
        fragments = {}

        @ channel.on("message")
        async def on_message(message):
            try:
                message = json.loads(message)
                if not "type" in message:
                    raise Exception()
            except:
                log_info("Invalid client message received")

            # process fragments first so that the full message can
            # be processed when it is finished receiving
            if message['type'] == 'FRAGMENT':
                if message['id'] not in fragments:
                    fragments[message['id']] = []
                fragments[message['id']].append(message['data'])
                if message['done'] == 1:
                    # reassmble message
                    message = json.loads(base64.b64decode(
                        "".join(fragments[message['id']])))
                else:
                    return

            if message['type'] == "STOP":
                # stop the tracker
                log_info("Connection closing...")
                for sender in pc.getSenders():
                    await sender.stop()
                for receiver in pc.getReceivers():
                    await receiver.stop()
                for transceiver in pc.getTransceivers():
                    await transceiver.stop()
                await pc.close()
                pcs.discard(pc)
                log_info("Connection closed successfully")
            elif message['type'] == "SUPPORT":
                # received supports, distribute to workers
                log_info("Received supports")
                await process_in_queue.coro_put({'type': "WORKER_SUPPORT", 'data': message['data']})
            elif message['type'] == "MODE":
                # change the mode
                log_info("Changing mode: {}".format(message['mode']))
                await process_in_queue.coro_put(message)
            elif message['type'] == "GET_WORKERS":
                # send workers to the client
                log_info("Workers requested")
                send_workers()
            else:
                log_info("Invalid message type: {}".format(message['type']))

    @ pc.on("connectionstatechange")
    async def on_connectionstatechange():
        log_info("Connection state is %s", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)
        if pc.iceConnectionState == 'disconnected':
            await pc.close()
            pcs.discard(pc)

    @ pc.on("track")
    def on_track(track):
        log_info("Track %s received", track.kind)

        if track.kind == "video":
            log_info("Creating a new track")
            pc.addTrack(
                ObjectTrackerTrack(
                    relay.subscribe(track)
                )
            )

        @ track.on("ended")
        async def on_ended():
            log_info("Track %s ended", track.kind)

    # handle offer
    await pc.setRemoteDescription(offer)

    # send answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()
    process_in_queue.close()
    img_out_queue.close()
    pull_proc.kill()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="WebRTC audio / video / data-channels demo"
    )
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for HTTP server (default: 8080)"
    )
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.cert_file:
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(
        app, access_log=None, host=args.host, port=args.port, ssl_context=ssl_context
    )
