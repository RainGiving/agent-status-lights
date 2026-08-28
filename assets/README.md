# assets

`icon.png` is the source for the app icon. `install.py build-app` regenerates
`build/AppIcon.icns` from it on every build, so replacing this file and
rebuilding is the whole workflow. Without it the build still succeeds and the
app just gets the generic icon.

What the file has to be:

- **Square**, ideally **1024×1024**.
- **Transparent outside the icon shape.** macOS does not round or mask an app
  icon for you, so an opaque source becomes a hard-edged square sitting among
  neighbours that are all rounded. `install.py icon` warns about this, and
  about a non-square or undersized source, rather than quietly shipping it.
- Laid out on Apple's grid: the rounded body fills **824 of the 1024** canvas
  (80.5%), centred, corner radius ~22.5% of the body. That is what makes it the
  same visual size as every other icon in the Dock.

The current file was produced from a 1254×1254 opaque render by clipping to the
artwork's own rounded card and re-drawing it on that grid.
