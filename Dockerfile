FROM library/debian:testing
MAINTAINER oyvindio
RUN apt-get update && apt-get -y upgrade && apt-get -y install git python3 python3-dev python3-pip sudo libxml2 libxml2-dev libxslt1.1 libxslt1-dev lzma lzma-dev
RUN useradd -d /home/cloudbot -m -s /bin/bash  cloudbot
USER cloudbot
WORKDIR /home/cloudbot
RUN git clone https://github.com/oyvindio/CloudBot.git && cd CloudBot && git checkout master
ADD config.json /home/cloudbot/CloudBot/config.json
USER root
RUN pip3 install -r CloudBot/requirements.txt
RUN chown cloudbot:cloudbot CloudBot/config.json
RUN apt-get -y purge python3-dev build-essential python3-pip libxslt1-dev libxml2-dev lzma-dev binutils gcc-4.9 cpp-4.9 git make && apt-get -y autoremove
USER cloudbot
WORKDIR /home/cloudbot/CloudBot
ENTRYPOINT ["python3", "cloudbot/__main__.py"]
