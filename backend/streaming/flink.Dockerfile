# backend/streaming/flink.Dockerfile
FROM flink:1.19.1-scala_2.12-java11

RUN apt-get update && \
    apt-get install -y python3 python3-pip python3-dev && \
    rm -rf /var/lib/apt/lists/* && \
    ln -s /usr/bin/python3 /usr/bin/python

COPY streaming/requirements.txt /opt/flink/streaming-requirements.txt
RUN pip3 install -r /opt/flink/streaming-requirements.txt

# flink-connector-kafka is the DataStream API's Kafka connector; it doesn't
# bundle kafka-clients itself, so that's added separately. Submitting a
# PyFlink DataStream job to a running session cluster needs BOTH on the
# per-job "user code" classloader specifically - the OperatorCoordinatorHolder
# that deserializes a KafkaSource's SplitEnumerator on the JobManager
# resolves classes through that classloader, not the system classloader,
# regardless of what's on /opt/flink/lib. That per-job classloader is
# populated from whatever single jar is passed via `flink run --jarfile
# <jar>` - so both jars are merged into one bundle below, referenced by
# realtime_metrics_job.py's own run instructions. (`-C`/`--classpath` does
# not reach this same classloader; only `--jarfile` does.)
#
# The merged bundle is deliberately NOT left in /opt/flink/lib alongside the
# two source jars: having the same Kafka classes loaded by both the system
# classloader (from /opt/flink/lib) and the per-job classloader (from
# --jarfile) at once causes real runtime failures - e.g. KafkaSink's
# ByteArraySerializer, loaded twice under two different classloaders,
# failing an `instanceof Serializer` check because Java classloading treats
# same-named classes from different classloaders as distinct types. The two
# source jars are removed after the bundle is built for exactly this reason.
ADD https://repo.maven.apache.org/maven2/org/apache/flink/flink-connector-kafka/3.2.0-1.19/flink-connector-kafka-3.2.0-1.19.jar \
    /tmp/kafka-jars/flink-connector-kafka.jar
ADD https://repo.maven.apache.org/maven2/org/apache/kafka/kafka-clients/3.4.0/kafka-clients-3.4.0.jar \
    /tmp/kafka-jars/kafka-clients.jar
RUN python3 -c "\
import zipfile; \
out = zipfile.ZipFile('/opt/flink/kafka-connector-bundle.jar', 'w', zipfile.ZIP_DEFLATED); \
seen = set(); \
[ \
    (seen.add(i.filename), out.writestr(i, z.read(i.filename))) \
    for src in ['/tmp/kafka-jars/flink-connector-kafka.jar', '/tmp/kafka-jars/kafka-clients.jar'] \
    for z in [zipfile.ZipFile(src)] \
    for i in z.infolist() if not i.filename.endswith('/') and i.filename not in seen \
]; \
out.close()" && \
    rm -rf /tmp/kafka-jars

COPY streaming/ /opt/flink/usrlib/streaming/

# The job imports `from streaming.metrics import ...` (package-relative to
# /opt/flink/usrlib, not to the script's own directory) - both the client
# process that submits the job AND the taskmanager's Python UDF worker
# subprocess (a separate process, doesn't inherit a `docker exec` shell's
# exported env) need this on PYTHONPATH to resolve that import.
ENV PYTHONPATH=/opt/flink/usrlib
