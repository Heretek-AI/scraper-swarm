# Smokescreen has no published image, so we build it from a pinned source commit (v0.1.0).
FROM golang:1 AS build
ARG SMOKESCREEN_REF=609eb8931420453daf5893509be0b25b21bd9edb
RUN git clone https://github.com/stripe/smokescreen /src \
 && cd /src && git checkout "${SMOKESCREEN_REF}" \
 && CGO_ENABLED=0 go build -trimpath -o /out/smokescreen .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/smokescreen /smokescreen
USER 65532:65532
EXPOSE 4750
ENTRYPOINT ["/smokescreen"]
# Extra deny ranges on top of Smokescreen's own "publicly routable only" resolution check.
CMD ["--listen-ip", "0.0.0.0", "--listen-port", "4750", \
     "--deny-range", "100.64.0.0/10", \
     "--deny-range", "169.254.0.0/16", \
     "--deny-range", "fc00::/7", \
     "--deny-range", "fe80::/10"]
