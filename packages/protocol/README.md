# CadPlot protocol

This package contains only the closed Pydantic wire models shared by the public gateway and the
licensed Windows workstation worker. It has no MCP, AutoCAD, COM, filesystem, network, or process
dependency. The gateway installs this as the exact `cadplot-protocol` distribution. The standalone
Windows `cadplot-mcp` wheel bundles the same source package so the existing offline release kit does
not depend on an unpublished registry artifact; compatibility tests bind both runtimes to the same
models and protocol version.
