from flask import Flask

def create_app():
    app = Flask(__name__)
    # Nothing here takes more than a form post with no fields.
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    from .guard import install
    install(app)

    from .routes import register_routes
    register_routes(app)

    return app