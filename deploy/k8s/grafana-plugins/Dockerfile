FROM grafana/grafana:12.4.2

USER root
RUN apk add --no-cache unzip

COPY grafana-exploretraces-app.zip /tmp/
COPY grafana-lokiexplore-app.zip /tmp/
COPY grafana-metricsdrilldown-app.zip /tmp/

RUN mkdir -p /var/lib/grafana/plugins && \
    unzip -qo /tmp/grafana-exploretraces-app.zip -d /var/lib/grafana/plugins && \
    unzip -qo /tmp/grafana-lokiexplore-app.zip -d /var/lib/grafana/plugins && \
    unzip -qo /tmp/grafana-metricsdrilldown-app.zip -d /var/lib/grafana/plugins && \
    rm -f /tmp/*.zip && \
    chown -R 472:0 /var/lib/grafana/plugins

USER grafana
