"""Native Discord controls for the read-only, ephemeral career summary."""
import math
import discord

def safe(value,limit=100):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]

class CareerView(discord.ui.View):
    def __init__(self,data,viewer_id,hub=None):
        super().__init__(timeout=600)
        self.data,self.viewer_id,self.mode,self.page=data,viewer_id,'all',0
        self.selector=discord.ui.Select(placeholder='All deliveries',options=[discord.SelectOption(label=label,value=value) for label,value in [('All deliveries','all'),('Real deliveries','real'),('Race deliveries','race'),('ETS2 deliveries','ETS2'),('ATS deliveries','ATS')]],row=0)
        self.selector.callback=self.filter_changed
        self.add_item(self.selector)
        self.previous=discord.ui.Button(label='Previous',style=discord.ButtonStyle.secondary,row=1)
        self.next=discord.ui.Button(label='Next',style=discord.ButtonStyle.secondary,row=1)
        self.previous.callback=self.go_previous;self.next.callback=self.go_next
        self.add_item(self.previous);self.add_item(self.next)
        if hub:self.add_item(discord.ui.Button(label='Open Hub Profile',url=hub['url'],row=1))
        self.sync_buttons()
    async def interaction_check(self,interaction):
        if interaction.user.id==self.viewer_id:return True
        await interaction.response.send_message('Open Career Details from the driver card to start your own view.',ephemeral=True)
        return False
    def rows(self):
        return [r for r in self.data['deliveries'] if self.mode=='all' or r['type']==self.mode or r['game']==self.mode]
    def sync_buttons(self):
        self.previous.disabled=self.page==0
        self.next.disabled=(self.page+1)*5>=len(self.rows())
    def embed(self):
        d=self.data;rows=self.rows();pages=max(1,math.ceil(len(rows)/5))
        embed=discord.Embed(title=f"{safe(d['name'],70)} · CAREER DETAILS",description='Driver '+(d.get('driverNumber') or '—'),colour=0x36C5F0)
        embed.add_field(name='Recorded best month',value=f"{d['bestMonthMiles']:,} mi" if d.get('bestMonthMiles') is not None else 'Unavailable')
        embed.add_field(name='Longest Real job',value=f"{d['longestJob']:,} mi" if d.get('longestJob') else 'Unavailable')
        embed.add_field(name='Monthly target',value=f"{d['monthlyRealMiles']:,} / 10,000 mi\n{d['targetPercent']:g}% achieved")
        positions='Monthly '+(f"#{d['monthlyPosition']}" if d.get('monthlyPosition') else '—')+' · All-time '+(f"#{d['allTimePosition']}" if d.get('allTimePosition') else '—')
        embed.add_field(name='Career standing',value=positions+f"\n{d['targetStreak']}-month target streak · Best monthly job count: "+(str(d['mostMonthlyJobs']) if d.get('mostMonthlyJobs') is not None else 'Unavailable'),inline=False)
        lines=[]
        for r in rows[self.page*5:self.page*5+5]:
            route=f"{safe(r['origin'],60)} → {safe(r['destination'],60)}" if r['origin'] and r['destination'] else 'Route unavailable'
            lines.append(f"**{r['game'] or 'Game unavailable'}** · {route}\n{r['miles']:,.0f} mi · {r['provider']} · {r['type'].title()}")
        embed.add_field(name=f'Recent credited deliveries · Page {self.page+1}/{pages}',value='\n\n'.join(lines) or 'No credited deliveries match this filter.',inline=False)
        embed.set_footer(text='Most recent 100 credited records · Recorded history only · Real and Race remain separate')
        return embed
    async def redraw(self,interaction):
        self.sync_buttons();await interaction.response.edit_message(embed=self.embed(),view=self,allowed_mentions=discord.AllowedMentions.none())
    async def filter_changed(self,interaction):
        self.mode=self.selector.values[0];self.page=0
        for option in self.selector.options:option.default=option.value==self.mode
        await self.redraw(interaction)
    async def go_previous(self,interaction):
        self.page=max(0,self.page-1);await self.redraw(interaction)
    async def go_next(self,interaction):
        self.page=min(max(0,math.ceil(len(self.rows())/5)-1),self.page+1);await self.redraw(interaction)
