ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3 ca-certificates
COPY app.py /app/app.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
