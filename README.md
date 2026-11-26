# SEED

**Sentiment and Evidence Extraction for Discussions**

SEED 是一个面向软件工程讨论的浏览器扩展项目。它面向需要快速读完长讨论的开发者，从当前 GitHub Issue 提取参与者的评价，并将每张评价卡片链接到网页中的原句。

在 Chrome 的 GitHub Issue 打开右下角的双瓣种子，面板提供「提取评价」「生成摘要」「设置 Key」。首版使用 SESAME 英文模型。

## 本地运行

使用 Python 3.11+、Node.js 20+ 和 Chrome。从仓库根目录启动本机后端。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 48729 --no-access-log
```

另开一个终端，在仓库根目录构建扩展。

```bash
npm --prefix extension ci
npm --prefix extension run build
```

在 Chrome 的 `chrome://extensions` 打开「开发者模式」，选择「加载已解压的扩展程序」，指向 `extension/dist/`。打开或刷新 GitHub Issue，点击右下角的种子，再选「设置 Key」。扩展设置页支持保存、替换和清除 Key。

## 使用流程

1. 点「提取评价」。本机 SESAME 对 Issue 正文和已加载的评论分句，显示正负面比例和评价卡片。卡片保留 Aspect、Opinion 和原句。
2. 点「生成摘要」选一种输入。完成提取并得到评价后，可以「基于评价生成」。也可以「直接全文生成」，用当前页面已加载的标题、正文及评论，跳过提取步骤。两个选项都会在提交前显示数据去向。
3. 「设置 Key」打开扩展自己的设置页。两种摘要共用本机保存的 DeepSeek API Key。点击评价或摘要里的编号可跳到所在消息。

重新打开 Issue 的悬浮面板时，会自动恢复匹配当前讨论内容的本机评价和最近一份摘要。两种摘要模式都可以复用已有结果。后端重启后，缓存仍保存在 `.local/results/`。

### 本机 SESAME 环境

提取复用已有 SESAME `pipeline/SESAME` 的英文 `laptop_model.h5`、SHAP、CoreNLP 和 Java 方面提取器。请先运行本机 `127.0.0.1:9000` 上的 CoreNLP。SEED 会尝试在桌面的 SESAME 工作区找到 `pipeline/.venv` 和管线文件，并从 Hugging Face 的本地缓存读取 RoBERTa。自定义安装可在启动后端前设置 `SEED_SESAME_PIPELINE`、`SEED_SESAME_PYTHON` 或 `SESAME_ROBERTA_PATH`。提取最多处理 160 句，SHAP 阶段会显示处理进度。

更新代码后，重启本机后端，在 `chrome://extensions` 的 SEED 卡片上点「重新加载」，再刷新 Issue 页。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [产品设计](docs/product-design.md) | 首版范围、面板交互、原文定位与版本规划 |
| [技术设计](docs/technical-design.md) | 扩展与后端分工、SESAME 接入、任务 API 与证据定位 |
| [开发计划](docs/development-plan.md) | 实现顺序、验收标准和发布前准备 |
| [AGENTS.md](AGENTS.md) | 仓库协作约定 |

SEED 的分析流程基于 SESAME 的 SHAP 引导句法分析。研究方法和原始实现见 [SESAME 仓库](https://github.com/nexpaliiz554/SESAME)。
