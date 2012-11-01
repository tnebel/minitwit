#!/bin/sh
ab -n 10000 -c 10 $1/public &
ab -n 10000 -c 10 -C session=$2 -p post_data -T application/x-www-form-urlencoded $1/add_message

