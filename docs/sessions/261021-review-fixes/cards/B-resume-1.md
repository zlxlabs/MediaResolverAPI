主脑验收 B 卡：版本测试、config.py、README、CHANGELOG、回滚与对账段落均通过，不需要再改。只有一处退回：

## 问题：CONTRIBUTING.md 服务器侧恢复命令的前提没有出处

新段落写「把镜像重标记为 compose 使用的 `latest`，再 `docker compose up -d`」。但：
- 仓内 `docker/docker-compose.yml` 是 `build:` 模式（无 `image:` 行），且 `env_file` 要求 `ISEC_ENV_FILE`（裸 `docker compose up -d` 会直接报「请用 isec docker compose ... 启动」）。这份 compose 显然不是服务器 `/srv/media-resolver-api/docker` 下 D3 使用的那份。
- 服务器上 D3 实际用哪个 compose、镜像名/标签怎么指定（`latest`？`IMAGE_TAG` 环境变量？ACR 全名？）、启动命令是否带 isec 前缀，卡面约束要求必须能在 `deploy.yml` 或 ci-templates `build-deploy.yml@v2` 找到出处。

## 要做的

1. 只读查看 ci-templates `build-deploy.yml@v2`（`gh api 'repos/zlxlabs/ci-templates/contents/.github/workflows/build-deploy.yml?ref=v2' --jq .content | base64 -d`，以及它调用的部署脚本，如有）中 SSH 部署步骤的真实命令：镜像名、标签变量、compose 文件来源、启动命令、`last_good_tag` 与 `pre-d3-baseline` 的使用方式。
2. 按真实命令改写 CONTRIBUTING.md 的服务器侧恢复示例，使镜像名/标签/启动命令与上游一致；每条命令在报告里给出上游出处（文件+行号）。
3. 若上游信息不足以写出可执行命令，就删掉具体命令，只保留「先 `gh workflow run deploy.yml --ref master` 补发；仍不行则按 ci-templates 部署步骤人工恢复到 `pre-d3-baseline`」的指引并链接上游文件，不写猜测命令。
4. 不 SSH 生产、不触发 workflow，修改范围仍只限 CONTRIBUTING.md 与本卡 progress 文件。commit + push 到原分支，报告追加本轮出处清单。
