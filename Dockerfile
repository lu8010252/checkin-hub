FROM node:20-alpine

ENV CREDITDADDY_HOME=/data
WORKDIR /app

# 项目零运行时依赖，直接把源码放进镜像即可，不需要 npm install
COPY . /app

EXPOSE 47860
CMD ["node", "bin/creditdaddy.js", "daemon", "--host", "0.0.0.0", "--port", "47860"]
