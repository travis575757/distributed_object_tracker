#!/bin/sh

CERT=/etc/letsencrypt/live/$URL/fullchain.pem
KEY=/etc/letsencrypt/live/$URL/privkey.pem
apt-get update && apt-get upgrade certbot
if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    echo "Cert exists. Checking if renew is needed..."
    certbot renew
    echo "Renew finished. Note auto-renew is not implemented, the server must be restarted before the cert expires"
else 
    echo "Cert does not exist. Acquiring..."
    certbot certonly --standalone -d $URL -m $EMAIL --agree-tos
fi
echo "starting server"
python3 /config/python/src/server.py --cert-file $CERT --key-file $KEY --port 443
