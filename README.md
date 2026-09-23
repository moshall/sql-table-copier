# SQL 常用表速查 · 一键复制给 AI

开新 SQL 会话前：勾选本次需求涉及的表 → 点「复制给 AI（完整口径）」→ 粘贴到会话开场。
复制的文本包含每张表的用途、粒度、业务日期/分区、关键列（含中文注释）、已知口径与坑，以及全局规则（dt 分区裁剪、T-2 验收窗口、方言注意点等）。

## 使用

- 在线：GitHub Pages 地址（index.html）
- 本地：双击 `index.html` 即可（零依赖、无网络请求、不连接任何数据源）

## 更新口径

口径与表清单维护在 `calibers.json`（人工口径层）。修改后在本仓库目录运行：

```bash
python3 build_page.py
```

`build_page.py` 会合并三类本地证据源重建 `index.html`：
1. `hue_table_spider/output_api*/` 的 DDL 文档（列与中文注释）
2. `picosql-resource-pack/registry/{tables,columns}.jsonl`（注册表兜底）
3. `calibers.json`（人工口径：用途/粒度/日期/join 键/口径与坑）

> 注意：`build_page.py` 依赖上述本地仓库路径，仅在对应工作区内可运行；`index.html` 为纯静态产物，可独立分发。

## 结构

| 文件 | 说明 |
|---|---|
| `index.html` | 单页工具（数据内嵌，双击即用） |
| `calibers.json` | 42 张常用表的口径注释 + 6 条全局规则 |
| `build_page.py` | 构建脚本（DDL 解析 + 注册表合并 + 渲染） |
