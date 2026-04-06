FROM grafana/grafana:12.4.2

USER root
RUN mkdir -p /var/lib/grafana/plugins

COPY plugins/ /var/lib/grafana/plugins/

RUN chown -R 472:0 /var/lib/grafana/plugins

USER grafana
