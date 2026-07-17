ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3
COPY app.py /app/app.py
COPY static /app/static
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
