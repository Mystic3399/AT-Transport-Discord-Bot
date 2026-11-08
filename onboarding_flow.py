"""A&T's concise, durable onboarding messages. Identity and mileage stay elsewhere."""
from pathlib import Path
from io import BytesIO
from functools import lru_cache
from PIL import Image, ImageOps
import discord

LOGO=Path(__file__).resolve().parent/'assets/tachograph/company-logo.webp'
SCHEMA='''
CREATE TABLE IF NOT EXISTS driver_onboarding_flow (
    discord_user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
    welcome_message_id BIGINT, phase INTEGER NOT NULL DEFAULT 0,
    last_failure_key TEXT, PRIMARY KEY(discord_user_id,channel_id)
);
'''


def panel(title,description,kind='info'):
    colour={'info':0x16BFF0,'success':0x36C997,'error':0xE47757}[kind]
    embed=discord.Embed(title=title,description=description,colour=colour)
    embed.set_author(name='A&T TRANSPORT LTD • DRIVER ONBOARDING')
    embed.set_footer(text='Driven Beyond Horizons • Need help? Ask A&T Management in this room.')
    return embed


@lru_cache(maxsize=1)
def logo_png():
    with Image.open(LOGO) as source:
        image=ImageOps.contain(source.convert("RGBA"),(144,64),Image.Resampling.LANCZOS)
    output=BytesIO()
    image.save(output,"PNG")
    return output.getvalue()


async def send(channel,embed,**kwargs):
    embed.set_thumbnail(url='attachment://at-onboarding.png')
    return await channel.send(embed=embed,file=discord.File(BytesIO(logo_png()),filename='at-onboarding.png'),
                              allowed_mentions=discord.AllowedMentions.none(),**kwargs)


def steam_signin(url):
    # Steam OpenID URLs exceed Discord's 512-character URL-button limit.
    return panel("🔐 CONNECT STEAM • Your secure sign-in",
        f"**[SIGN IN WITH STEAM — CLICK HERE]({url})**\n\n"
        "**1.** Open the link above.\n"
        "**2.** Sign in to your own Steam account.\n"
        "**3.** Return here — the bot will show the result and your next step.\n\n"
        "This private link expires in **10 minutes**. Do not share it.")


def stage(view):
    if view['steam_status']!='Verified': return 1
    if not any(a['status'] in {'Linked','Verified'} for a in view['accounts'].values()): return 2
    return 3


def instruction(view):
    step=stage(view)
    if step==1:
        return panel('🔐 STEP 1 OF 3 • Connect Steam',
            '**Type this in this room:**\n```\n!linksteam\n```\n'
            'Then click the **Sign in with Steam** link in the bot’s reply, sign in to your own account, and return here.\n\n'
            '✅ The bot will tell you when Steam is verified.\n'
            'Keep your sign-in link private. It expires after 10 minutes.')
    if step==2:
        return panel('🚛 STEP 2 OF 3 • Link a tracking account',
            '**Choose TrucksBook, Trucky, or both.**\nUse the account you drive with for A&T.\n\n'
            '**TrucksBook:**\n`!trucksbook YOUR_ID`\n'
            '**Trucky:**\n`!trucky YOUR_ID`\n\n'
            '**Replace `YOUR_ID` with the number from your profile URL.**\n'
            'Example only: `!trucksbook 123456`\nDo not enter your username or the whole URL.\n\n'
            'The bot will check the account and show the result here.')
    accounts='\n'.join('✅ **'+name.title()+':** '+discord.utils.escape_markdown(a['display_name'] or 'Linked')
        for name,a in view['accounts'].items() if a['status'] in {'Linked','Verified'})
    return panel('🏁 STEP 3 OF 3 • Finish your registration',
        accounts+'\n\n**Check that these are your accounts.**\n'
        'Want to add the other tracker first? Use `!trucksbook YOUR_ID` or `!trucky YOUR_ID`.\n\n'
        '**Ready? Type:**\n```\n!confirm\n```\n'
        'Wrong account? Ask Management here before confirming.\n\n'
        'Linking connects your profile. Driving mileage is checked separately.')


def completion():
    return panel('✅ YOU’RE REGISTERED • Welcome to A&T!',
        '**Your accounts are connected.**\n\n'
        'Open <#1553821609331138580> and type **`!profile`**.\n'
        'Use **View Back / View Front** to flip your card.\n\n'
        '🌍 **`/change country`** — choose your flag.\n'
        '🚛 **`/change truck`** — choose your fleet truck.\n'
        '📋 **`/myregistration`** — check linked accounts.\n\n'
        '**This private room closes in 30 seconds.**\nYour saved profile stays with you.', 'success')


async def advance(pool,channel,user_id,view):
    phase=stage(view)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('INSERT INTO driver_onboarding_flow(discord_user_id,channel_id) VALUES($1,$2) ON CONFLICT DO NOTHING',user_id,channel.id)
            saved=await conn.fetchrow('SELECT phase,welcome_message_id FROM driver_onboarding_flow WHERE discord_user_id=$1 AND channel_id=$2 FOR UPDATE',user_id,channel.id)
            if not saved['welcome_message_id']:
                message=await send(channel,panel('🪪 WELCOME ABOARD • What happens next?',
                    'This is your **private A&T setup room**.\n\n'
                    '**1. Connect Steam** — confirm your gaming account.\n'
                    '**2. Link your tracker** — TrucksBook, Trucky, or both.\n'
                    '**3. Finish** — open your personal driver card.\n\n'
                    'Follow **one message at a time**. The bot will guide you.\n'
                    'Only you and authorised A&T staff can see this room.\n'
                    'Started by mistake? Type `!cancel`.'))
                await conn.execute('UPDATE driver_onboarding_flow SET welcome_message_id=$3 WHERE discord_user_id=$1 AND channel_id=$2',user_id,channel.id,message.id)
            if phase!=saved['phase']:
                if saved['phase']==1 and phase>=2:
                    await send(channel,panel('✅ STEAM VERIFIED',
                        '**Your Steam sign-in worked.**\nYou can now link your tracking account below.', 'success'))
                await send(channel,instruction(view))
                await conn.execute('UPDATE driver_onboarding_flow SET phase=$3,last_failure_key=NULL WHERE discord_user_id=$1 AND channel_id=$2',user_id,channel.id,phase)


async def failure(pool,channel,user_id,reason,event_key):
    explanations={
        'expired':'Your sign-in link has expired or was already used.',
        'cancelled':'Steam sign-in was cancelled. Your account was not changed.',
        'unavailable':'Steam is temporarily unavailable. Please try again shortly.',
        'failed':'The Steam sign-in could not be verified, or that Steam account is already connected elsewhere.'}
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('INSERT INTO driver_onboarding_flow(discord_user_id,channel_id) VALUES($1,$2) ON CONFLICT DO NOTHING',user_id,channel.id)
            old=await conn.fetchval('SELECT last_failure_key FROM driver_onboarding_flow WHERE discord_user_id=$1 AND channel_id=$2 FOR UPDATE',user_id,channel.id)
            if old==event_key: return
            await send(channel,panel('⚠️ STEAM NOT CONNECTED • Try again',
                explanations.get(reason,explanations['failed'])+'\n\n'
                '**Return to this room and type:**\n```\n!linksteam\n```\n'
                'Use the new link and sign in to your own Steam account.\n'
                'Still stuck? Ask Management in this room.','error'))
            await conn.execute('UPDATE driver_onboarding_flow SET last_failure_key=$3 WHERE discord_user_id=$1 AND channel_id=$2',user_id,channel.id,event_key)
