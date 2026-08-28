# assets

Drop the app icon here as `icon.png`.

- **Square**, ideally **1024×1024**. `install.py icon` warns about anything else
  rather than silently producing a squashed or soft icon.
- Transparent background if you want the usual rounded-app look; macOS does not
  round or mask it for you.

`install.py build-app` (and `install-app`) regenerate `build/AppIcon.icns` from
it on every build, so replacing this file and rebuilding is the whole workflow.
Without it the build still succeeds and the app just gets the generic icon.
