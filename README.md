# 创业管理课程 AI 研讨工作流 + Zotero Buddy

本仓库把课程项目初始化工作流与 Zotero Buddy 本地文献管理 Skill 放在一起，供厦门大学“创业管理理论与前沿”课程同学部署和复用。

## 仓库结构

- `course-workflow/`：课程项目初始化 Skill、参考规范、启动提示词及空白 DOCX/PDF 模板。
- `skills/zoterobuddy/`：Zotero Buddy Skill、Python 标准库脚本、参考说明和测试。
- `一键部署提示词.md`：复制到支持 Skill/本地文件操作的 Agent 的部署指令。
- `NOTICE.md`：内容范围、隐私处理与来源说明。

## 快速开始

1. 克隆或下载本仓库。
2. 在能读取本地文件并控制本地应用的 Agent 中打开仓库目录。
3. 粘贴 `一键部署提示词.md` 的完整内容。
4. 由 Agent 检查工具、说明缺失项及影响；所有软件安装、Skill 写入和 Zotero 文献分类写入均须先取得你的明确同意。
5. 每周课程作业另开一个对话，按 `course-workflow/` 中的流程推进。

本仓库不包含可直接使用的个人 Zotero 配置。首次使用时，Buddy 应依据当前 Zotero 库现场读取分类结构；不要把本机 Collection Key、文献库导出或附件同步到仓库。

课程流程遇到 Zotero 检索、DOI 添加、Collection 分类或 `#` 标签时，应结合 `skills/zoterobuddy/SKILL.md` 执行；以 Buddy 对单批数据写入的预览和授权规则为准。

## 工具依赖

- Zotero Desktop + Zotero Buddy：支持本地文献检索及经用户批准后的分类和标签整理。
- LibreOffice 或 Word/WPS：支持 DOCX/PDF 导出与版面复核。
- Office CLI（`officecli`）：可选，用于 Office 文件的结构化命令行操作。
- Python 3：Buddy 脚本仅使用 Python 标准库。

缺少工具时，Agent 需逐项询问是否协助安装；用户拒绝后解释具体工作流影响和替代方式。


