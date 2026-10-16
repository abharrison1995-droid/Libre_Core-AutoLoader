#!/usr/bin/env bash
set -eu
printf 'host_utc='; date -u +%Y-%m-%dT%H:%M:%SZ
printf 'host_uid='; id
printf 'kernel='; uname -a
printf 'host_netns='; readlink /proc/self/ns/net
printf 'unshare='; unshare --version | head -1
printf 'iproute2='; ip -Version
printf 'sysfs_mount='; findmnt -T /sys -n -o TARGET,SOURCE,FSTYPE,OPTIONS
printf '\n--- user-mapped network namespace ---\n'
unshare --user --map-root-user --net -- sh -c '
set -eu
printf "child_netns="; readlink /proc/self/ns/net
printf "sys_class_net="; python3 -c "import os; print(sorted(os.listdir(\"/sys/class/net\")))"
printf "socket_if_nameindex="; python3 -c "import socket; print(socket.if_nameindex())"
printf "ip_link_json="; ip -j link show
printf "ip_addr_json="; ip -j address show
printf "ip_v4_routes_json="; ip -j -4 route show table all
printf "ip_v6_routes_json="; ip -j -6 route show table all
printf "ip_v4_rules_json="; ip -j -4 rule show
printf "ip_v6_rules_json="; ip -j -6 rule show
printf "proc_dev="; cat /proc/net/dev
printf "proc_route="; cat /proc/net/route
printf "proc_ipv6_route="; cat /proc/net/ipv6_route
printf "proc_if_inet6="; cat /proc/net/if_inet6
'
