FROM node:20-bookworm-slim

ENV CREDITDADDY_HOME=/data
WORKDIR /app

# 项目零运行时依赖，直接把源码放进镜像即可，不需要 npm install
# 用 Debian(glibc) 而不是 Alpine(musl)：Qoder 的 UMID 设备身份组件是 glibc 二进制，
# 在 musl 下会以 spawn ENOENT（找不到动态链接器）失败
COPY . /app

EXPOSE 47860
CMD ["node", "bin/creditdaddy.js", "daemon", "--host", "0.0.0.0", "--port", "47860"]
