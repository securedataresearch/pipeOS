# Streaming

The box streams one capture to up to four providers at once. Two modes:

- **Restream** an existing feed (a URL or a capture device like
  `/dev/video0`).
- **Browser** — render a web page to video: the box runs a headless browser
  at the resolution and frame rate you set, and encodes what it shows.

## Providers

Each provider is a slot: name, RTMP URL, stream key, an on/off switch, and
optionally its **own bitrate**. Providers with different bitrates get their
own encoder from the same capture — one capture, several encodes.

Numbers that work in practice:

- **YouTube: 14000k.** Its re-encoder wants a fat master; judge the result
  on the public watch page at 1080p60, not the low-fps Studio preview.
- **Twitch: about 6000k.** It chokes on much more.
- **1080p60 with VAAPI** (hardware encode on the Intel GPU) carries this
  comfortably.

The default bitrate applies to providers without one of their own.

## Boot behaviour

**Start streaming automatically at boot** is on by default; saving a config
with a live provider also enables the stream service. Turn the boot switch
off for a box that should only stream when told to.

## When it stutters

Check the stream log first (**Show stream log**). If one provider stutters
while another is clean, the problem is that provider's leg or its bitrate —
the shared encoder is usually blameless. Mid-broadcast format changes can
show a squashed picture for a minute while the provider's transcoder
resettles; that is them, not the box.
