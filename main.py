import sys
import re
import json
from typing import Union
import traceback
import datetime
import secrets
import zlib
import asyncio
import aiofiles
import discord
from discord.ext import commands, slash

Ctx = commands.Context # type alias
CATEGORY_NAME = 'ConvoSplit Temporary Channels'
MY_PERMS = discord.PermissionOverwrite(
    read_messages=True, read_message_history=True, send_messages=True,
    manage_permissions=True, embed_links=True
)

async def error(ctx: Union[discord.abc.Messageable, slash.Context], msg):
    """Send a red Error embed."""
    emb = discord.Embed(
        title='Error',
        description=str(msg),
        color=0xff0000
    )
    if isinstance(ctx, slash.Context):
        await ctx.respond(embeds=[emb])
    else:
        await ctx.send(embed=emb)

GUILD = 712215625178546219
client = slash.SlashBot(
    '"', debug_guild=GUILD,
    description='Split conversation into temporary channels')

async def check_perms(ctx: slash.Context):
    """Ensure all necessary permissions are given."""
    perms: discord.Permissions = ctx.channel.permissions_for(ctx.me)
    if not all((
        perms.read_message_history, perms.read_messages, perms.send_messages,
        perms.manage_channels, perms.embed_links
    )):
        raise commands.CheckFailure('Missing permissions')

@client.event
async def on_command_error(ctx, exc):
    """Silence some errors, defer some, send some, and print the rest."""
    if hasattr(ctx.command, 'on_error'):
        return
    if isinstance(ctx, Ctx):
        cog = ctx.cog
        if cog and hasattr(cog, 'cog_error'):
            return
    if isinstance(exc, (
        commands.BotMissingPermissions,
        commands.MissingPermissions,
        commands.MissingRequiredArgument,
        commands.BadArgument,
        commands.CommandOnCooldown,
    )):
        return await error(ctx, exc)
    if isinstance(exc, (
        commands.CheckFailure,
        commands.CommandNotFound,
        commands.TooManyArguments,
    )):
        return
    print('Ignoring exception in command {}:\n{}'.format(
        ctx.command,
        ''.join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))
    ), end='', file=sys.stderr)

@client.event
async def on_guild_join(guild: discord.Guild):
    """Check that the ConvoSplit category exists upon joining a guild.
    Create it if it doesn't, and send a "hi, I exist" message.
    """
    cat = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    for channel in guild.text_channels:
        try:
            if cat:
                await channel.send(f"Hi! Run {client.command_prefix}split to "
                                    "split the conversation into a new "
                                    "temporary channel.")
            else:
                await channel.send("Hi! I'm about to create a channel category "
                                    "for ConvoSplit channels to be created in. "
                                    "Once I have, you can run "
                                    + client.command_prefix
                                    + " to split the conversation into a new "
                                    "temporary channel.")
        except discord.Forbidden:
            continue
        else:
            break
    else:
        try:
            await error(guild.owner, f"I can't send messages in {guild.name}!")
        except:
            pass
        await guild.leave()
        return
    if not cat:
        cat = await guild.create_category(
            CATEGORY_NAME, reason="Creating category for temporary convos.")
    await cat.edit(overwrites=MY_PERMS)

def convert_timeout(argument: str) -> datetime.timedelta:
    """Convert timeouts into timedeltas."""
    pieces = re.split(r'[^0-9]+', argument)[::-1]
    try:
        pieces = list(map(int, pieces))
    except ValueError:
        raise commands.BadArgument("timeout must have a number") from None
    if not pieces:
        raise commands.BadArgument("timeout must have a number") from None
    multiples = [1, 60, 3_600, 86_400, 2_592_000, 31_536_000]
    seconds: int = sum(i * j for i, j in zip(pieces, multiples))
    return datetime.timedelta(seconds=seconds)


memberopts = [slash.Option(
    description=f'Member #{i+1} you want to limit discussion to.',
    type=slash.ApplicationCommandOptionType.USER)
              for i in range(5)]
timeoutopt = slash.Option(
    description='How long (HH:MM:SS) you want to wait '
    'before deleting the channel. Default 05:00')

@client.slash_cmd()
async def split(
    ctx: slash.Context,
    timeout: timeoutopt = datetime.timedelta(minutes=5),
    member1: memberopts[0] = None, member2: memberopts[1] = None,
    member3: memberopts[2] = None, member4: memberopts[3] = None,
    member5: memberopts[4] = None
):
    """Split conversation into a new temporary channel."""
    await check_perms(ctx)
    if not isinstance(timeout, datetime.timedelta):
        try:
            timeout = convert_timeout(timeout)
        except commands.BadArgument as exc:
            await error(ctx, str(exc))
            return
    members = list(filter(None, [member1, member2, member3, member4, member5]))
    # create temporary channel
    cat: discord.CategoryChannel = discord.utils.get(
        await ctx.guild.fetch_channels(), name=CATEGORY_NAME)
    if not cat:
        await error(ctx, "Category for temporary conversations is deleted!")
        return
    key = secrets.token_hex(4)
    name = 'convo-' + key
    # set permissions for private channel
    permows = {}
    permows[ctx.me] = MY_PERMS
    if members:
        permows[ctx.guild.default_role] \
            = discord.PermissionOverwrite(send_messages=False)
        for member in members:
            permows[member] = discord.PermissionOverwrite(send_messages=True)
        # don't let the convo splitter lock themself out
        permows[ctx.author] = discord.PermissionOverwrite(send_messages=True)
    channel = await cat.create_text_channel(
        name, overwrites=permows, reason="By request of user.")
    # mention the new channel
    if members:
        people = '(' + ', '.join(m.mention for m in members) + ') '
    else:
        people = ''
    start = datetime.datetime.utcnow()
    await ctx.respond(f"Those in {ctx.author.mention}'s conversation {people}"
                      f"please move to {channel.mention} (convo {key})")
    # record messages
    msgs = []
    while 1:
        try:
            msg = await client.wait_for(
                'message', check=lambda m: m.channel.id == channel.id,
                timeout=timeout.total_seconds())
        except asyncio.TimeoutError:
            break
        if (
            msg.author.id == ctx.author.id
            and getattr(msg.type, 'value', msg.type) == 20 # interaction
            and msg.content.lstrip().casefold().startswith('</exit')
        ):
            break
        msgs.append(msg)
    await channel.send('Goodbye.')
    # immediately lock the channel
    await channel.edit(
        overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(
                read_messages=False, send_messages=False),
            ctx.me: MY_PERMS
        },
        reason="Locking the channel")
    # dump messages to file
    filename = f'convos/{start:%Y-%m-%d %Hh%Mm%Ss} ' \
        f'- {msg.created_at:%Y-%m-%d %Hh%Mm%Ss}.txt'
    async with aiofiles.open(filename, 'w') as dump:
        for msg in msgs:
            await dump.write(
                f'\n--- {msg.created_at.isoformat()} '
                + (f'(edited {msg.edited_at.isoformat()}) '
                   if msg.edited_at else '')
                + f'{msg.author!s} '
                f'({msg.author.display_name}) (user {msg.author.id}) '
                f'(message {msg.id}) ---\n')
            await dump.write(msg.content)
    # delete channel and send log
    await channel.delete()
    await ctx.send(f'Convo {key} finished:', file=discord.File(filename))

@client.slash_cmd()
async def exit(ctx: slash.Context):
    """Conclude a split convo."""
    await ctx.respond(rtype=slash.InteractionResponseType.AcknowledgeWithSource)

@client.slash_cmd()
async def hello(ctx: slash.Context):
    """Hello World!"""
    await ctx.respond('Hello World!')

@client.slash_cmd()
async def say(ctx: slash.Context, message: slash.Option(
    description='The message to send', required=True
)):
    """Say something silently."""
    await ctx.respond(message, rtype=slash.InteractionResponseType.ChannelMessage)

@client.slash_cmd()
async def stop(ctx: slash.Context):
    """Stop the bot."""
    if ctx.author.id != client.app_info.owner.id:
        await error(ctx, 'You are not the owner.')
        return
    await ctx.respond(rtype=slash.InteractionResponseType.AcknowledgeWithSource)
    await client.close()

with open('convosplit.txt') as f:
    TOKEN = f.readline().strip()

try:
    client.run(TOKEN)
finally:
    print('Goodbye.')
