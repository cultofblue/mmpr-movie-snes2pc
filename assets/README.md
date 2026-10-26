# Optional icon

The build adds an icon to the game if these files exist here:

| File | Used for |
|---|---|
| `icon.ico` | the .exe file icon in Windows Explorer (Windows builds) |
| `icon64.rgba` | the window and taskbar icon: 64×64 pixels, raw RGBA bytes (16,384 bytes) |

They're git-ignored, so a personal icon is never published by accident. To make `icon64.rgba` from a PNG with Python and Pillow:

```python
from PIL import Image
open("icon64.rgba", "wb").write(Image.open("icon.png").convert("RGBA").resize((64, 64)).tobytes())
```
