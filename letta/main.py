import typer

from letta.cli.cli import server
from letta.cli.secrets_cli import secrets_app

app = typer.Typer(pretty_exceptions_enable=False)

# Register server as both the default command and as a subcommand
app.command(name="server")(server)

# v0.16.40 (E-1): secrets admin commands (re-encrypt legacy plaintext)
app.add_typer(secrets_app, name="secrets")


# Also make server the default when no command is specified
@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        # If no subcommand is specified, run the server
        server()


if __name__ == "__main__":
    app()
