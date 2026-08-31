# assets

图标的主题是彗星绕圈 —— 也就是「执行中」状态在外圈上的默认动画。

| 文件 | 作用 |
| --- | --- |
| `HALO.icon/` | 液态玻璃图标源：`icon.json` 定义白色底、玻璃、高光与半透明，`Assets/comet.png` 是彗星图层。`install.py install-app` 用 actool 把它编译成 `Assets.car` 和 icns。actool 只随完整 Xcode 提供 |
| `icon.png` | 平面后备图标：白色圆角底加同一幅彗星。机器上没有 actool 时，`install.py` 退回用它生成 icns |
| `render_icon.py` | 上面两幅图的生成器，逐像素渲染（锥形渐隐拖尾加辉光彗头），依赖 numpy 和 Pillow。改配色或几何参数后重跑一次即可 |

重新生成：

```bash
python3 assets/render_icon.py
/usr/bin/python3 scripts/install.py install-app
```

`icon.png` 的要求（`install.py icon` 会对不满足的情况给出警告）：

- 正方形，1024×1024。
- 图标形状之外透明。macOS 不会替你裁切圆角，不透明的源会在 Dock 里显示成一块方形。
- 按 Apple 的网格排布：圆角主体占 1024 画布的 824（80.5%），居中，圆角半径约为
  主体的 22.5%。这是它在 Dock 里和其他图标视觉等大的原因。
