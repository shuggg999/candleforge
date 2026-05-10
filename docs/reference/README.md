# `docs/reference/` —— 参考实现库

## 这里放什么

不被运行时引用、但**设计思路值得参考**的实现样本。

| 文件 | 来源 | 参考价值 |
|---|---|---|
| `qlib_style_data_validator.py` | 原 `src/validators/crypto_data_validator.py`（脏改动期产物，未被活代码引用） | Qlib 风格的金融数据离群值检测、补齐、validation pipeline。`add-volume-detection` change 设计 z-score / 分位数滚动窗口时可借鉴它的统计方法 |

## 与 `docs/legacy/` 的区别

| 维度 | `docs/legacy/` | `docs/reference/` |
|---|---|---|
| 来源 | 本仓库写过、被替换的旧实现 | 任何来源（自研废弃 / 第三方思路 / 论文实现） |
| 读法 | 看"我们以前怎么搞砸的"，避免重复踩坑 | 看"别人/旧方案怎么解决的"，借思路 |
| 是否完整 | 通常完整但已停止维护 | 可以是片段、Snippet、伪代码 |

## 运行时约束

跟 `docs/legacy/` 同：`src/` 下任何代码 **不得 import** 这里的内容（spec: `baseline-infrastructure` 第 9 条）。
