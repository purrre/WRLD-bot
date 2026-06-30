import discord, random
from discord.ext import bridge, commands
from discord.ui import Container, Section, TextDisplay, Thumbnail
import time, httpx

LOADING_EMOJIS = [
    "<a:takeoff:1517041443175268477>",
    "<a:gravityflip:1517046487622615050>",
    "<a:sway:1517046344680738866>",
    "<a:radarping:1517046319317782549>",
    "<a:glitchwave:1517046212178477159>",
    "<a:hovering:1517046296416878663>",
]
from config import colors, endpoints
from utils.functions import httpcall
from utils.components import loading, createView


class UtilsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.region = None
        self.country = None

    @bridge.bridge_command(usage='ping', description='Pings juicewrldapi to check the status', aliases=['p'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ping(self, ctx):
        msg = await ctx.reply(random.choice(LOADING_EMOJIS))
        hosts = {
            'main': (endpoints.jwa, ['/']),
            'api': (endpoints.jwa, ['/juicewrld/']),
            'media (master)': (endpoints.media, ['/status/'])
        }
        status_lines = []
        async with httpx.AsyncClient(timeout=8.0) as client:
            for name, (base_url, paths) in hosts.items():
                start = time.perf_counter()
                online = False
                used_url = None
                for p in paths:
                    url = f'{base_url}{p}'
                    try:
                        r = await client.get(url)
                        if r.status_code < 400:
                            online = True
                            used_url = url
                            break
                    except Exception:
                        continue
                ping_ms = (time.perf_counter() - start) * 1000
                if online:
                    status_lines.append(f"<:status_online:1443125660552921142> {name.title()}")
                    status_lines.append(f"-# {ping_ms:.2f}ms")
                else:
                    status_lines.append(f"<:status_offline:1443125840501014572> {name.title()}")
                    status_lines.append("-# down")

        cont = Container()
        header_lines = ["### juicewrldapi Status"]
        header_lines.extend(status_lines)
        accessory = Thumbnail("https://i.imgur.gg/AYK6hmG-hero-removebg-preview.png")
        if accessory:
            cont.add_item(Section(TextDisplay("\n".join(header_lines)), accessory=accessory))
        else:
            cont.add_text("\n".join(header_lines))
        cont.add_separator(divider=True)
        cont.add_text(f"-# Use `{ctx.prefix}jwa` for classic layout\n-# Use `{ctx.prefix}wrld` for bot ping")
        view = createView(cont)
        await msg.edit(content=None, embed=None, view=view)

    @bridge.bridge_command(name='jwa', usage='jwa', description='Pings juicewrldapi (classic embed layout)')
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def jwa(self, ctx):
        msg = await ctx.reply(embed=await loading('PING'))
        if not self.country or self.country.lower() == 'unknown':
            ip = await httpcall('https://api.ipify.org', expect_json=False)
            r = await httpcall(f'https://ipinfo.io/{ip[1]}/json')
            self.country = r[1].get('country', 'unknown')
            self.region = r[1].get('region', '')
        embed = discord.Embed(title='juicewrldapi Status', url=endpoints.jwa, color=colors.main)
        hosts = {
            'main': (endpoints.jwa, ['/']),
            'api': (endpoints.jwa, ['/juicewrld/']),
            'media (master)': (endpoints.media, ['/status/'])
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            for name, (base_url, paths) in hosts.items():
                start = time.perf_counter()
                online = False
                used_url = None
                for p in paths:
                    url = f'{base_url}{p}'
                    try:
                        r = await client.get(url)
                        if r.status_code < 400:
                            online = True
                            used_url = url
                            break
                    except Exception:
                        continue
                ping_ms = (time.perf_counter() - start) * 1000
                if online:
                    embed.add_field(
                        name=f'<:status_online:1443125660552921142> {name.title()}',
                        value=f'Ping: {ping_ms:.2f} ms',
                        inline=True
                    )
                else:
                    embed.add_field(
                        name=f'<:status_offline:1443125840501014572> {name.title()}',
                        value=f"Couldn't reach host :(",
                        inline=True
                    )
        embed.set_footer(text=f'{self.bot.user.name} | Pinged from {self.region}, {self.country}', icon_url=self.bot.user.avatar.url)
        await msg.edit(embed=embed)

    @bridge.bridge_command(usage='apistats', description='Shows basic juicewrldapi statistics')
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def apistats(self, ctx):
        msg = await ctx.reply(embed=await loading('API'))
        success1, mAPI = await httpcall(endpoints.media_status)
        success2, gAPI = await httpcall(endpoints.plays_stats)
        if not success1 or not success2:
            return await ctx.reply(embed=mAPI)
        embed = discord.Embed(title='juicewrldapi Statistics', color=colors.main, description=f'Total Files: {mAPI.get('total_files', 'N/A')} | Commits: {mAPI.get('total_commits', 'N/A')} | Data Size: {round(int(mAPI.get('total_size_bytes', '0')) / 1073741824 , 2)} GB', url=f'{endpoints.jwa}/stats')
        embed.set_thumbnail(url='https://raw.githubusercontent.com/HackinHood/juicewrldapi-desktop/refs/heads/master/assets/icon.png')
        embed.add_field(name='__Plays__', value=f'- Total Plays: {gAPI.get('total_plays', 'N/A')}'
                        f'\n- Unique Songs Played: {gAPI.get('total_songs_with_plays', 'N/A')}'
                        f'\n- Unique Albums Played: {gAPI.get('total_albums_with_plays', 'N/A')}'
                        f'\n- Unique Eras Played: {gAPI.get('total_eras_with_plays', 'N/A')}',
                        inline=True)
        embed.add_field(
            name='__Top Categories__',
            value='\n'.join([f'- {c['category'].replace('_', ' ').title()}: {c['count']} plays' for c in sorted(gAPI.get('category_breakdown', []), key=lambda x: x['count'], reverse=True)[:5]]),
            inline=True
        )
        embed.add_field(
            name='__Top Songs__',
            value='\n'.join([
                f'- {s['name']} ({s['era_name']}): **{s['play_count']}** plays'
                for s in sorted(gAPI.get('top_songs', []), key=lambda x: x['play_count'], reverse=True)[:5]
            ]),
            inline=False
        )
        embed.set_footer(text=self.bot.user.name, icon_url=self.bot.user.avatar.url)
        await msg.edit(embed=embed)


def setup(bot):
    bot.add_cog(UtilsCog(bot))