# 医学文献检索与参考文献爬虫

独立的医学文献工作台，提供两条流程：

1. 按主题、标题、作者或 DOI 检索医学文献，合并并核对 DOI/PMID。
2. 上传含可复制文字的 PDF，提取编号参考文献，匹配文献记录，人工复核后批量获取有明确许可的开放获取 PDF。

检索使用 PubMed E-utilities、Europe PMC、OpenAlex 和 Crossref 的结构化接口；开放获取位置使用 Europe PMC、OpenAlex 和 Unpaywall。项目不会通过登录墙、镜像站或出版社网页绕过访问限制。Scrapling 适合以后处理已获授权的网页解析场景，但不替代这些文献数据库接口。

## 技术栈

React + TypeScript + Vite；FastAPI + Pydantic + SQLAlchemy + Alembic；PostgreSQL；Redis + Celery；RustFS。项目独立运行，不依赖其他 Lab 项目的源码、数据库或服务。当前 Compose 仅绑定本机 127.0.0.1:9200，不包含用户认证，不应直接公开部署。

## 启动

1. 复制 .env.example 为 .env，修改数据库和 RustFS 密码，并填写用于 API 礼貌访问的 CONTACT_EMAIL。按需要填写 NCBI_API_KEY、OPENALEX_API_KEY。若本机 TUN 代理把公开域名解析到 198.18.0.0/15，可在本机 .env 显式设置 ALLOW_FAKE_IP_DNS=true；其他环境保持 false。
2. 运行 docker compose up --build -d。
3. 打开 http://127.0.0.1:9200。健康检查：docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read())"。
4. 查看任务：docker compose logs -f worker；普通停止：docker compose down（保留数据卷）。

首次启动自动执行 Alembic 迁移。变更前备份 PostgreSQL 和 RustFS 数据卷；恢复时先恢复两者，再在同一代码版本上启动。docker compose down -v 会删除数据，不用于普通停止。

## 开发与检查

在 Mac mini 的本项目目录：

~~~bash
cd backend
uv sync --extra test --locked
uv run --extra test pytest -q
cd ../frontend
npm ci
npm run build
~~~

API 的 OpenAPI 文档可在运行中的服务 /docs 查看（服务容器内部端口 8000）。

## 数据与边界

- 检索保留查询词、来源、检索时间、DOI、PMID 和题录。不同来源的记录按 DOI/PMID 合并。
- PDF 引文的自动匹配采用 DOI 精确匹配或标题词覆盖度；低分候选须人工确认。下载任务仅接受已核实 DOI，并保存来源 URL、许可、大小、SHA-256 和状态。
- 扫描版、加密版、无参考文献标题或非编号参考文献的 PDF 会明确报错；当前版本不对其臆造引文。扫描版 OCR 可在独立 worker 中追加 RapidOCR。
- 来源 API 的结果和 OA 地址可能变化；HTTP 成功不等于取得可用 PDF。下载器检查 HTTPS、公开地址、类型、PDF 文件头和 50 MB 上限。
- 本仓库只含原创代码和合成测试数据。请勿提交真实文献 PDF、账号凭据、业务数据或第三方版权材料。
