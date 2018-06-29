FROM vdc-os-registry.af.multis.p.fti.net/hebex/ubuntu:16.04

ENV TRAVIS=yes
ENV PYTHON=python3
ENV DISTRO=xenial

WORKDIR /app

COPY app .
RUN pwd \
&& apt-get update \
&& apt-get -y install build-essential automake autoconf libtool git libssl-dev redis-server ${PYTHON}-pip \
&& ./travis.sh -d -o
