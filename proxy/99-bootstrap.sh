#!/bin/sh
# Bootstrap script for nginx proxy container.
#
# 1. Install initial config if volume is empty (first boot)
# 2. Watch config file and hot-reload nginx when controller rewrites it
#
# The proxy-conf volume hides the image's /etc/nginx/conf.d. If the volume is
# empty (first boot), install the 100%-stable bootstrap so nginx can start
# before the controller has rendered anything.
if [ ! -s /etc/nginx/conf.d/default.conf ]; then
    cp /etc/nginx/bootstrap.conf /etc/nginx/conf.d/default.conf
fi

# Watch the shared config and hot-reload nginx when the controller rewrites it.
# This runs in the background as a daemon process.
(
    last=$(md5sum /etc/nginx/conf.d/default.conf)
    while sleep 1; do
        cur=$(md5sum /etc/nginx/conf.d/default.conf)
        if [ "$cur" != "$last" ] && nginx -t -q; then
            nginx -s reload && last=$cur
        fi
    done
) &
