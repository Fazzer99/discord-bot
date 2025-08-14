from discord import app_commands

def require_manage_channels():
    return app_commands.default_permissions(manage_channels=True)

def require_manage_guild():
    return app_commands.default_permissions(manage_guild=True)
