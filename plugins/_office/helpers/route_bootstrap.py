from __future__ import annotations


def install_route_hooks() -> None:
    from helpers.ui_server import UiServerRuntime

    if getattr(UiServerRuntime, "_a0_office_route_hooks_installed", False):
        return

    original_register_http_routes = UiServerRuntime.register_http_routes
    original_build_asgi_app = UiServerRuntime.build_asgi_app

    def register_http_routes(self):
        result = original_register_http_routes(self)
        from plugins._office.helpers.wopi_routes import register_wopi_routes

        register_wopi_routes(self.webapp)
        return result

    def build_asgi_app(self, startup_monitor):
        from socketio import ASGIApp
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from uvicorn.middleware.wsgi import WSGIMiddleware

        from helpers import fasta2a_server, mcp_server
        from plugins._office.helpers.office_proxy import OfficeProxy

        with startup_monitor.stage("wsgi.middleware.create"):
            wsgi_app = WSGIMiddleware(self.webapp)

        with startup_monitor.stage("mcp.proxy.init"):
            mcp_app = mcp_server.DynamicMcpProxy.get_instance()

        with startup_monitor.stage("a2a.proxy.init"):
            a2a_app = fasta2a_server.DynamicA2AProxy.get_instance()

        with startup_monitor.stage("starlette.app.create"):
            starlette_app = Starlette(
                routes=[
                    Mount("/office", app=OfficeProxy(self.webapp)),
                    Mount("/mcp", app=mcp_app),
                    Mount("/a2a", app=a2a_app),
                    Mount("/", app=wsgi_app),
                ],
                lifespan=startup_monitor.lifespan(),
            )

        with startup_monitor.stage("socketio.asgi.create"):
            return ASGIApp(self.socketio_server, other_asgi_app=starlette_app)

    UiServerRuntime.register_http_routes = register_http_routes
    UiServerRuntime.build_asgi_app = build_asgi_app
    UiServerRuntime._a0_office_route_hooks_installed = True
    UiServerRuntime._a0_office_original_build_asgi_app = original_build_asgi_app


def is_installed() -> bool:
    from helpers.ui_server import UiServerRuntime

    return bool(getattr(UiServerRuntime, "_a0_office_route_hooks_installed", False))
