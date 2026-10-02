# ASR-managed URF runtime source

This directory is the reproducible source context used by the guided URF/TGIF
Setup Wizard. The installer builds the images locally on the ASL3 host; recovery
binaries are never installation inputs.

- `Dockerfile`: native URFD build with ASR client and USRP patches.
- `Dockerfile.tcd-arm-qemu`: ARMv7 transcoder image with software vocoders, executed through bundled QEMU where required.
- `Dockerfile.tgif`: TGIF sidecar using the pinned, SHA-256 verified DVSwitch MMDVM_Bridge binary.
- `tgif/`: runtime TGIF/MMDVM configuration plus the ASR TGIF↔URF protocol adapter.
- `patches/`: ASR modifications copied from the existing URFWIL working
  source on josh-Latitude-5420. The original working tree was not edited.
- URFD: https://github.com/nostar/urfd at
  `bd0c114e43adc3df4c17823bc5693328425d0450`.
- TCD: https://github.com/nostar/tcd at
  `ff104384b3d1c51bf8f7f9f83d7940dffda0b076`.
- IMBE vocoder: https://github.com/nostar/imbe_vocoder at
  `03423cb00b8e9ba1be164493e8b98b5bd7c1508c`.
- MD380 vocoder: https://github.com/nostar/md380_vocoder at
  `0ebd2761d441db354f859280873d4286856f3e73`.

The upstream source headers contain GPL notices. The patches replace upstream
files and retain their attribution. The MD380 build retrieves
`md380fw.img` and `md380ram.img` over HTTPS from dudetronics.com and checks
their SHA-256 digests before compilation; the upstream Makefile's live HTTP
downloads are disabled. Review the firmware's distribution terms before distributing prebuilt images.
The Setup Wizard does not commit or redistribute those firmware images: the local
Docker build retrieves them, verifies their hashes, and compiles the software
vocoder in place. A fresh installer builds these inputs and never copies recovery
binaries.
