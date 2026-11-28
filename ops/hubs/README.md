# Hub host tuning

`99-netoholics-voip.conf` sets the UDP receive buffers every Netoholics hub
needs. With the kernel default (about 208 KB), Asterisk's IAX socket on 4569
overflows during busy nets, and incoming voice packets are dropped. Operators
then cut in and out for everyone (#346).

Install it on every hub, including rebuilt hubs and Texas 674984, before the hub
goes live:

```text
sudo install -m 0644 ops/hubs/99-netoholics-voip.conf /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/99-netoholics-voip.conf
sudo systemctl restart asterisk   # off-air: briefly drops all links
```

Verify the setup:

```text
sudo ss -uanm | awk '/:4569 /{getline; print}'   # expect rb67108864
grep '^Udp:' /proc/net/snmp                       # RcvbufErrors must stop climbing
```
