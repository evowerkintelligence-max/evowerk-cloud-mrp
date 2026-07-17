# backend/streaming/flink.Dockerfile
FROM flink:1.19.1-scala_2.12-java11

RUN apt-get update && \
    apt-get install -y python3 python3-pip python3-dev && \
    rm -rf /var/lib/apt/lists/* && \
    ln -s /usr/bin/python3 /usr/bin/python

COPY streaming/requirements.txt /opt/flink/streaming-requirements.txt
RUN pip3 install -r /opt/flink/streaming-requirements.txt

ADD https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.2.0-1.19/flink-sql-connector-kafka-3.2.0-1.19.jar \
    /opt/flink/lib/flink-sql-connector-kafka.jar

COPY streaming/ /opt/flink/usrlib/streaming/
