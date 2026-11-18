#!/bin/sh
# Install the nginx conf on .147, get a cert (if missing), reload. Needs sudo on .147.
set -e
cd "$(dirname "$0")/.."
HOST=192.168.0.147
NAME=flashcard.cloud-handson.com
CONF=deploy/nginx/$NAME.conf
# HTTP-only variant (first server block) for the ACME challenge before the cert exists.
awk '/^server \{/{n++} n<=1' "$CONF" > /tmp/$NAME.http.conf
scp -q "$CONF" /tmp/$NAME.http.conf $HOST:/tmp/
ssh -t $HOST "set -e
if sudo test -f /etc/letsencrypt/live/$NAME/fullchain.pem; then
  echo 'cert exists'
else
  sudo cp /tmp/$NAME.http.conf /etc/nginx/conf.d/$NAME.conf
  sudo nginx -t && sudo systemctl reload nginx
  sudo certbot certonly --webroot -w /var/www/html -d $NAME --non-interactive --agree-tos --keep-until-expiring
fi
sudo cp /tmp/$NAME.conf /etc/nginx/conf.d/$NAME.conf
sudo nginx -t && sudo systemctl reload nginx
rm -f /tmp/$NAME.conf /tmp/$NAME.http.conf
curl -s -o /dev/null -w 'via 147 -> backend: %{http_code}\n' -H 'Host: $NAME' http://192.168.0.208:8810/healthz"
rm -f /tmp/$NAME.http.conf
