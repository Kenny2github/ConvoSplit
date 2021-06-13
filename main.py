import asyncio
from datetime import datetime
import json
import os
import secrets
import traceback
from typing import List, Optional
import aiofiles
import discord
from discord.ext import slash
from discord.ext import commands

CONFIG_FILE = 'convosplit.json'

ERROR_FMT = '\N{CROSS MARK} **Error**: '
NO_CAT_ERROR = """
Missing channel category for temporary conversations.
Please ask someone with permission to do so to create \
a channel category with the word "ConvoSplit" anywhere \
in the name.
""".strip()
NO_PERMS_ERROR = """
Missing permissions to create new channels and edit their permissions.
Please ask someone with permission to do so to grant me the ability to
- create new channels
- edit their permissions
in the ConvoSplit channel category.
""".strip()
BOT_DESC = 'Split conversations into temporary channels'
MEMBER_DESC = 'Member #%s you want to limit discussion to.'
TIMEOUT_DESC = """
How long (in minutes) the channel can remain inactive before it is deleted. \
Default 5.
""".strip()
CHANNEL_DESC = """
Once the conversation ends, (try to) send its archive to this channel.
""".strip()
NEW_CHANNEL_REASON = 'Creating temporary channel to split a conversation.'
LOCK_REASON = 'Locking the channel while saving its messages.'
DELETE_REASON = 'Conversation over.'
SPLIT_RESPONSE = """
Those in {author.mention}'s conversation \
please move to {channel.mention} (convo {key}).
""".strip()
CONVO_DONE = 'Conversation {key} finished:'
PERMS_WARNING = """
\N{WARNING SIGN} **Warning**: I cannot send messages with a file as a bot \
in this channel or the `dest_channel` (if specified). If the conversation \
(including ending inactivity, if any) lasts more than 10 minutes, its log \
will be lost!
""".strip()
FILENAME_FMT = '{name}---{start:%Y-%m-%d %H-%M-%S}---{end:%Y-%m-%d %H-%M-%S}.txt'
SEPARATOR = '--New Message Starts After Two Line Feeds After This Line'

MY_PERMS = discord.PermissionOverwrite(
    read_messages=True, read_message_history=True, send_messages=True,
    manage_permissions=True, embed_links=True
)

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

client = slash.SlashBot(
    description=BOT_DESC,
    command_prefix=r'\/',
    help_command=None,
    activity=discord.Activity(
        type=discord.ActivityType.watching, name='/split'),
    debug_guild=CONFIG.get('guild_id', None),
    resolve_not_fetch=False,
    fetch_if_not_get=True
)

async def send_error(method, msg):
    await method(ERROR_FMT + msg)

@client.event
async def on_command_error(ctx, exc):
    """Silence some errors, defer some, send some, and print the rest."""
    if hasattr(ctx.command, 'on_error'):
        return
    if isinstance(ctx, commands.Context):
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
        return await send_error(ctx.send, exc)
    if isinstance(exc, (
        commands.CheckFailure,
        commands.CommandNotFound,
        commands.TooManyArguments,
    )):
        return
    print(
        'Ignoring exception in command {}:'.format(ctx.command),
        ''.join(traceback.format_exception(
            type(exc), exc, exc.__traceback__
        )),
        sep='\n', flush=True
    )

def cat_check(cat: discord.CategoryChannel):
    return 'convosplit' in cat.name.casefold()

async def create_channel(
    ctx: slash.Context,
    members: List[discord.Member]
) -> Optional[discord.TextChannel]:
    """Create a new temporary channel."""
    cat: discord.CategoryChannel = discord.utils.find(
        cat_check, await ctx.guild.fetch_channels())
    if not cat:
        await send_error(ctx.respond, NO_CAT_ERROR)
        return None
    new_channel_name = 'convo-' + secrets.token_hex(4)
    overwrites = {}
    # mirror originating channel permissions
    overwrites.update(ctx.channel.overwrites)
    # I reign supreme
    overwrites[ctx.me] = MY_PERMS
    overwrites.setdefault(ctx.guild.default_role, discord.PermissionOverwrite(
        read_messages=True, send_messages=True))
    if members:
        # in case the originating channel allows more than just these
        # people to send messages, explicitly disallow sending messages
        # for everyone else
        for user_or_role, overws in overwrites.items():
            if user_or_role is ctx.me:
                continue # don't deny myself lol
            overws.send_messages = False
        # explicitly allow sending messages for the actual members
        for member in members:
            overwrites[member] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True)
        # don't let the invoker lock themself out
        overwrites[ctx.author] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True)
    # create and return the channel with its name and permissions
    try:
        channel = await cat.create_text_channel(
            new_channel_name, reason=NEW_CHANNEL_REASON)
        await channel.edit(overwrites=overwrites, reason=NEW_CHANNEL_REASON)
    except discord.Forbidden:
        await send_error(ctx.respond, NO_PERMS_ERROR)
        return None
    return channel

async def notify_members(
    ctx: slash.Context,
    channel: discord.TextChannel,
    dest: discord.TextChannel,
    members: List[discord.Member]
):
    """Complete the response and notify members if necessary."""
    key = channel.name.split('-')[-1]
    await ctx.respond(SPLIT_RESPONSE.format(
        author=ctx.author,
        channel=channel,
        key=key
    ))
    p1 = ctx.channel.permissions_for(ctx.me)
    p1 = (p1.send_messages and p1.read_messages and p1.attach_files)
    p2 = dest.permissions_for(ctx.me)
    p2 = (p2.send_messages and p2.read_messages and p2.attach_files)
    if not (p2 or p1):
        await ctx.webhook.send(PERMS_WARNING)
    if members:
        await ctx.webhook.send(' '.join(m.mention for m in members))

def is_goodbye(content: str) -> bool:
    content = content.lstrip().casefold()
    return content.startswith('goodbye')

async def await_end(
    channel: discord.TextChannel,
    timeout: int
) -> List[discord.Message]:
    """Wait for the conversation to end, by timeout or /exit."""
    while 1:
        try:
            msg = await client.wait_for(
                'message', check=lambda m: m.channel.id == channel.id,
                timeout=timeout * 60)
        except asyncio.TimeoutError:
            await channel.send('Goodbye.')
            return
        if msg.author.id == client.user.id and is_goodbye(msg.content):
            return

async def format_message(msg: discord.Message) -> str:
    """Format a message into multipart-like format."""
    lines = []
    lines.append(f'Message-Id: {msg.id}')
    lines.append(f'Author: {msg.author!s} ({msg.author.id})')
    lines.append(f'Sent: {msg.created_at.isoformat()}')
    if msg.edited_at:
        lines.append(f'Edited: {msg.edited_at.isoformat()}')
    if msg.reference:
        if msg.type == discord.MessageType.pins_add:
            lines.append(f'Pins: {msg.reference.message_id}')
        else:
            lines.append(f'Reply-To: {msg.reference.message_id}')
    for r in msg.reactions:
        users = []
        async for user in r.users():
            users.append(f'{user!s} ({user.id})')
        lines.append(f'Reaction: {r!s}; {", ".join(users)}')
    for f in msg.attachments:
        lines.append('Attachment: name={}, content_type={}, url={}'.format(
            f.filename, f.content_type or 'Unspecified', f.url
        ))
    for e in msg.embeds:
        lines.append(f'Embed: {json.dumps(e.to_dict())}')
    lines.append('')
    if msg.is_system():
        lines.append(msg.system_content)
    else:
        lines.append(msg.content)
    return '\n'.join(lines)

async def save_messages(
    start: datetime,
    channel: discord.TextChannel
) -> str:
    """Save messages to a file. Return its name."""
    filename = os.path.join('convos', FILENAME_FMT.format(
        name=channel.name,
        start=start,
        end=datetime.utcnow()
    ))
    async with aiofiles.open(filename, 'w', encoding='utf8') as dump:
        async for msg in channel.history(oldest_first=True):
            if msg.author.id == client.user.id and is_goodbye(msg.content):
                continue
            await dump.write(SEPARATOR + '\n'
                             + (await format_message(msg)) + '\n')
        await dump.write(SEPARATOR + '--\n')
    return filename

async def conclude(
    ctx: slash.Context,
    start: datetime,
    channel: discord.TextChannel,
    filename: str,
    dest: Optional[discord.TextChannel]
):
    """Delete the channel and send its archive."""
    await channel.delete(reason=DELETE_REASON)
    content = CONVO_DONE.format(key=channel.name.split('-')[-1])
    attachment = discord.File(filename)
    if dest:
        try:
            await dest.send(content, file=attachment)
        except discord.Forbidden:
            # failing to send can still close the file
            attachment = discord.File(filename)
        else:
            os.unlink(filename)
            return
    if (datetime.utcnow() - start).seconds < (10 * 60):
        await ctx.webhook.send(content, file=attachment)
    else:
        try:
            await ctx.send(content, file=attachment)
        except discord.Forbidden:
            pass # welp, we warned them and we tried
    os.unlink(filename)

memberopts = [slash.Option(
    description=MEMBER_DESC % (i + 1),
    type=slash.ApplicationCommandOptionType.USER) for i in range(5)]
timeoutopt = slash.Option(
    description=TIMEOUT_DESC,
    type=slash.ApplicationCommandOptionType.INTEGER)
channelopt = slash.Option(
    description=CHANNEL_DESC,
    type=slash.ApplicationCommandOptionType.CHANNEL)

@client.slash_cmd()
async def split(
    ctx: slash.Context,
    timeout: timeoutopt = 5, member1: memberopts[0] = None,
    member2: memberopts[1] = None, member3: memberopts[2] = None,
    member4: memberopts[3] = None, member5: memberopts[4] = None,
    dest_channel: channelopt = None
):
    """Split the conversation into a new temporary channel."""
    members = [m for m in [member1, member2, member3, member4, member5] if m]
    await ctx.respond(deferred=True)
    channel = await create_channel(ctx, members)
    if not channel:
        return
    await notify_members(ctx, channel, dest_channel or ctx.channel, members)
    start = datetime.utcnow()
    await await_end(channel, timeout)
    # lock the channel
    await channel.edit(
        overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(
                read_messages=False, send_messages=False),
            ctx.me: MY_PERMS
        },
        reason=LOCK_REASON
    )
    filename = await save_messages(start, channel)
    await conclude(ctx, start, channel, filename, dest_channel)

@client.slash_cmd()
async def exit(ctx: slash.Context):
    """End the conversation and archive the channel."""
    await ctx.respond('Goodbye.')

@client.slash_cmd()
async def invite(ctx: slash.Context):
    """Get a link to invite this bot to your server."""
    await ctx.respond(CONFIG['url'], ephemeral=True)

async def wakeup():
    while 1:
        try:
            await asyncio.sleep(1)
        except:
            await client.close()
            return

try:
    client.loop.create_task(wakeup())
    client.run(CONFIG['token'])
finally:
    print('Goodbye.')
