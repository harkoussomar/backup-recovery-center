# Compatibility Contract

`v0.1.0-alpha.6` is deliberately narrow.

## Supported architecture

The shell integration expects an Illogical Impulse / end-4 `dots-hyprland`
layout with:

```text
<II_ROOT>/GlobalStates.qml
<II_ROOT>/panelFamilies/IllogicalImpulseFamily.qml
<II_ROOT>/modules/common/
```

`GlobalStates.qml` must expose a top-level `Singleton { ... }`.

`IllogicalImpulseFamily.qml` must have a normal QML import section and a
top-level closing brace into which a `PanelLoader` can be added.

Importantly, **Arch Remote is not required**. The installer and its functional
shell-editor tests use a fixture that contains no Arch Remote module.

## Runtime requirements

- `qs -c ii` names the active Quickshell configuration.
- Hyprland exposes `hyprctl binds -j` and `hyprctl layers`.
- The created window maps as `namespace: quickshell:backupRecovery`.

## Release pinning

Before a public non-alpha release, CI/VM testing should pin and record a known
working upstream Illogical Impulse commit. `alpha.6` uses a validated shell
contract rather than claiming compatibility with every upstream revision.
