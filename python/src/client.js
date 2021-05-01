
// peer connection
var pc = null;
var dc = null;
var webcam_stream = null;
// current list of supports
var supports_available = 3;

var useRemote = true

function createPeerConnection() {

    var config = {
        sdpSemantics: 'unified-plan', // seems to work without this, will keep here incase it is needed for possible backwards compatability 
        iceServers: [{urls: ['stun:stun.l.google.com:19302']}] // use a public google server for ICE
    }

    pc = new RTCPeerConnection(config);

    return pc

}

function enableTrackButtons() {
    var multi = document.getElementById('multi_button');
    var aggr = document.getElementById('aggregate_button');
    var reset = document.getElementById('reset_button');

    multi.style = '';
    aggr.style = '';
    reset.style = '';
}

function disableTrackButtons() {
    var multi = document.getElementById('multi_button');
    var aggr = document.getElementById('aggregate_button');
    var reset = document.getElementById('reset_button');

    multi.style = 'display: none;';
    aggr.style = 'display: none;';
    reset.style = 'display: none;';
}


function useWebcam(callback = undefined) {
    navigator.mediaDevices.getUserMedia({video: true}).then(function(stream) {
            stream.getTracks().forEach(function(track) {
                if (track.kind == 'video') {
                    document.getElementById('video').srcObject = stream;
                    document.getElementById('tracker_buttons').style = 'display: none;';
                    document.getElementById('support_buttons').style = '';
                    if (callback !== undefined)
                        callback()
                }
            });
        }, function(err) {
            alert('Could not acquire media: ' + err);
        })
}

function useMultiTracker() {
    dc.send(JSON.stringify({"type":"MODE","mode":1}))
    useTracker();
}

function useAggregateTracker() {
    dc.send(JSON.stringify({"type":"MODE","mode":2}))
    useTracker();
}

function useTracker(callback = undefined) {
    if (pc.getRemoteStreams)
        pc.getRemoteStreams().forEach(function(stream) {
                stream.getTracks().forEach(function(track) {
                    if (track.kind == 'video') {
                        document.getElementById('video').srcObject = stream;
                        document.getElementById('support_buttons').style = 'display: none;';
                        document.getElementById('tracker_buttons').style = '';
                        if (callback !== undefined)
                            callback()
                    }
                });
            });
    else
        alert('Video stream disconnected, please restart tracker');
}

function negotiate() {
    // Overview of WebRTC Connection Process
    // https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Connectivity
    // https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Signaling_and_video_calling
    return pc.createOffer().then(function(offer) {
        // configure our end of the connection using the offer given by our web browser
        // this also begins ICE gathering
        return pc.setLocalDescription(offer);
    }).then(function() {
        // wait for ICE gathering to complete
        return new Promise(function(resolve) {
            if (pc.iceGatheringState === 'complete') {
                resolve();
            } else {
                function checkState() {
                    if (pc.iceGatheringState === 'complete') {
                        pc.removeEventListener('icegatheringstatechange', checkState);
                        resolve();
                    }
                }
                pc.addEventListener('icegatheringstatechange', checkState);
            }
        });
    }).then(function() {
        // perform offer signaling via POST on the remove web server
        var offer = pc.localDescription;
        return fetch('/offer', {

            body: JSON.stringify({
                sdp: offer.sdp,
                type: offer.type,
                video_transform: "cartoon"
            }),
            headers: {
                'Content-Type': 'application/json'
            },
            method: 'POST'
        });
    }).then(function(response) {
        return response.json();
    }).then(function(answer) {
        if (answer['error'] !== undefined) {
            alert(answer['error'])
        } else {
            return pc.setRemoteDescription(answer);
        }
    }).then(function() {
        useWebcam(function() {
            var video_container = document.getElementById('video');
            var callback = function() {
                document.getElementById('content_container').style = ''
                video_container.removeEventListener('loadeddata', callback);
            }
            video_container.addEventListener('loadeddata', callback, false);
        })
    }).catch(function(e) {
        alert(e);
    });
}



// https://stackoverflow.com/questions/17130395/real-mouse-position-in-canvas
function  getMousePos(canvas, evt) {
  var rect = canvas.getBoundingClientRect(), // abs. size of element
      scaleX = canvas.width / rect.width,    // relationship bitmap vs. element for X
      scaleY = canvas.height / rect.height;  // relationship bitmap vs. element for Y

  return {
    x: (evt.clientX - rect.left) * scaleX,   // scale mouse coordinates after they have
    y: (evt.clientY - rect.top) * scaleY,    // been adjusted to be relative to element
  }
}

// used for messages which are too large for WebRTC data channel
// operates by fragmenting and encapsulating in messages sent to the server
// TODO: fix so this works with USVStrings
function sendLargeMessage(data) {
    // calculate the message size
    var text_encoder = new TextEncoder()
    var text_decoder = new TextDecoder()
    var byte_array = text_encoder.encode(btoa(data))
    // calculate maximum possible message size we can send
    var mtu = pc.sctp.maxMessageSize;
    var id = Date.now();
    var msg_frame = {'type':'FRAGMENT','id':id,'done':0,'data':""}
    var max_msg_size = mtu - text_encoder.encode(JSON.stringify(msg_frame)).length
    // number of messages we will be sending
    var msg_count = Math.ceil(byte_array.length / max_msg_size);
    // send fragments
    for (var i = 0; i < msg_count; i++) {
        var msg = JSON.parse(JSON.stringify(msg_frame))
        var payload = byte_array.slice(i * max_msg_size, Math.min((i + 1) * max_msg_size, byte_array.length))
        msg['data'] = text_decoder.decode(payload)
        if (i == (msg_count - 1))
            msg['done'] = 1
        dc.send(JSON.stringify(msg))
    }
}

// send the selected supports to the server
function sendSupports() {
    var support_container = document.getElementById("info_container");
    var support_count = support_container.childElementCount;
    // server side verification of the number of supports will also be performed
    if (support_count == supports_available) {
        var supports = [];  
        for (var i = 0; i < support_container.childNodes.length; i++) {
            var node = support_container.childNodes[i];
            if (node.nodeType == Node.ELEMENT_NODE)
                supports.push({"img":node.dataset.img_data,"bbox":node.dataset.bbox})
                if (supports.length == supports_available) {
                    // send the server the support images
                    sendLargeMessage(JSON.stringify({"type":"SUPPORT","data":supports}))
                }
                // support_container.childNodes[i].toBlob(function(img_blob) {
                //     var reader = new FileReader();
                //     reader.readAsDataURL(img_blob); 
                //     reader.onloadend = function() {
                //         var base64data = reader.result.split(",")[1];
                //         supports.push(base64data)
                //         if (supports.length == supports_available) {
                //             // send the server the support images
                //             sendLargeMessage(JSON.stringify({"type":"SUPPORT","data":supports}))
                //         }
                //     }
                // }, 'image/png')
        }
    } else {
        alert('Not enough supports!')
    }
}

function getWorkersAndSelectSupports() {
    dc.send(JSON.stringify({"type":"GET_WORKERS"}))
}

// display the webcam to the user, have then click and drag a support box, then return to the original view
function selectSupports() {
    disableTrackButtons();
    // common DOM elements
    var video = document.getElementById('video');
    var support_container = document.getElementById("info_container")
    // use for drawing bounding box
    var p1,p2;
    // if supports are already selected then clear them and restart
    var support_count = support_container.childElementCount;
    if (support_count >= supports_available)
        while (support_container.firstChild)
            support_container.removeChild(support_container.firstChild)
    var videoListener = function() {
        // remove listener
        video.removeEventListener('click',videoListener)
        var canvas = document.getElementById('selection_canvas')
        canvas.height = video.videoHeight;
        canvas.width = video.videoWidth;
        var ctx = canvas.getContext('2d');
        // capture the current video frame
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        var image = ctx.getImageData(0, 0, canvas.width, canvas.height);


        // add a camera flash effect
        var counter = 50;
        var interval = setInterval(function() {
            ctx.fillStyle = 'white';
            ctx.globalAlpha = 1.0;
            ctx.putImageData(image, 0, 0);
            ctx.globalAlpha = counter / 50.0;
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            counter--;
            if (counter == 0)
                clearInterval(interval);
        }, 10);

        canvas.style.display = "block";
        var mouseMove = e => {
            p2 = getMousePos(canvas, e)
            // draw the box while moving the mouse
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.putImageData(image, 0, 0);
            ctx.fillStyle = 'green';
            ctx.globalAlpha = 1.0;
            ctx.beginPath()
            ctx.rect(p1.x,p1.y,p2.x - p1.x,p2.y - p1.y)
            ctx.stroke();
        }
        var mouseDown = e => {
            p1 = getMousePos(canvas, e)
            canvas.addEventListener('mousemove', mouseMove)
        }
        var mouseUp = e => {
            p2 = getMousePos(canvas, e)
            // do not select if box is too small
            if ( (p2.x - p1.x) < 16 || (p2.y - p1.y) == 16 )
                return
            // clean up listeners
            canvas.removeEventListener('mousedown',mouseDown)
            canvas.removeEventListener('mousemove',mouseMove)
            canvas.removeEventListener('mouseup',mouseUp)

            // extract the support and draw inside a canvas in the info div
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.putImageData(image, 0, 0);
            var support = ctx.getImageData(p1.x,p1.y,p2.x - p1.x,p2.y - p1.y)
            var support_view = document.createElement('canvas');
            support_view.className += " p-2"; // add some padding
            support_container.appendChild(support_view);
            support_view.width = support.width;
            support_view.height = support.height;
            support_view.getContext('2d').putImageData(support,0,0)

            support_view.dataset.bbox = [p1.x,p1.y,p2.x - p1.x,p2.y - p1.y]
            // save image data to the display canvas 
            canvas.toBlob(function(img_blob) {
                var reader = new FileReader();
                reader.readAsDataURL(img_blob); 
                reader.onloadend = function() {
                    var base64data = reader.result.split(",")[1];
                    support_view.dataset.img_data = base64data;
                    // send support data if applicable
                    if (support_count >= supports_available) {
                        sendSupports()
                        enableTrackButtons();
                    }
                }
            }, 'image/png')

            // hide canvas
            canvas.style.display = "none";
            // select additional support boxes if needed
            var support_count = document.getElementById("info_container").childElementCount;
            if (support_count < supports_available) {
                selectSupports();
            }
        }
        canvas.addEventListener('mousedown', mouseDown)
        canvas.addEventListener('mouseup', mouseUp)
    }
    video.addEventListener("click", videoListener);
}

function startSelection() {
    document.getElementById('tut_container').style = 'display: none;'
}

function onmessage(evt) {
    console.log(evt)
    try {
        message = JSON.parse(evt.data) 
        if (!('type' in message))
            throw new Error()
    } catch (err) {
        console.log('Invalid message received')
        console.log(err)
    }

    switch (message['type']) {
        case 'SUPPORT':
            supports_available = message['workers'].length;
            var worker_container = document.getElementById('worker_container')
            while (worker_container.firstChild) {
                worker_container.removeChild(worker_container.firstChild);
            }
            for (var worker of message['workers']) {
                var container = document.createElement('div');
                var color = worker['color'].reverse().join(',');
                var color_inv = [];
                for (var c of worker['color'])
                    color_inv.push(255 - c);
                color_inv = color_inv.join(',')
                console.log(color)
                container.style = `background-color: rgba(${color},1); color: rgb(${color_inv});`
                container.className = 'p-3 mb-2'
                container.innerHTML = worker['id'];
                worker_container.appendChild(container)
            }
            console.log(message)
            if (supports_available > 0)
                selectSupports();  
            else
                alert('No workers available!')
            break;
        default:
            console.log('Invalid message type: ${message["type"]}')
            break;
    }

}

function start() {

    // setup peer connection and messaging

    pc = createPeerConnection();

    dc = pc.createDataChannel('data',{"ordered": true})
    dc.onmessage = onmessage

    navigator.mediaDevices.getUserMedia({video: true}).then(function(stream) {
            stream.getTracks().forEach(function(track) {
                webcam_stream = pc.addTrack(track, stream);
            });
            return negotiate();
        }, function(err) {
            alert('Could not acquire media: ' + err);
        });

}

function stop() {
    dc.send(JSON.stringify({"type":"STOP"}))
    
    setTimeout(function() {
        if (dc) {
            dc.close();
        }

        // close transceivers
        if (pc.getTransceivers) {
            pc.getTransceivers().forEach(function(transceiver) {
                if (transceiver.stop) {
                    transceiver.stop();
                }
            });
        }

        // close local audio / video
        pc.getSenders().forEach(function(sender) {
            sender.track.stop();
        });

        // close peer connection
        setTimeout(function() {
            pc.close();
        }, 500);
    },500)
}

start()
