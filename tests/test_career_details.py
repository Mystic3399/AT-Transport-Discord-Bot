import ast
import json
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import pytest
import career_details as c
import career_ui
import tachograph
import website_company_bridge as bridge

UK=ZoneInfo('Europe/London')
def row(miles,when,kind='real',provider='trucky',payload=None):
 return dict(job_id='123',discord_user_id=42,miles=Decimal(str(miles)),delivered_at=datetime.fromisoformat(when),mileage_type=kind,provider=provider,source_payload=payload)

def test_monthly_streak_does_not_break_during_an_unfinished_month():
 rows=[row(10000,'2026-09-30T22:30:00+00:00'),row(10000,'2026-10-30T12:00:00+00:00'),row(9999,'2026-11-02T12:00:00+00:00'),row(999999,'2026-11-03T12:00:00+00:00','race')]
 # First delivery falls in September in London, despite BST.
 result=c.summarise(rows,datetime(2026,11,28,tzinfo=UK))
 assert result['targetStreak']==2
 assert result['monthlyRealMiles']==9999
 assert result['bestMonthMiles']==10000
 rows.append(row(1,'2026-11-04T12:00:00+00:00',provider='trucksbook'))
 assert c.summarise(rows,datetime(2026,11,28,tzinfo=UK))['targetStreak']==3
 # A missed completed month ends the streak.
 assert c.summarise(rows,datetime(2027,1,3,tzinfo=UK))['targetStreak']==0

def test_uk_month_boundary_and_fractional_miles():
 rows=[row('6000.6','2026-03-31T23:30:00+00:00'),row('3999.4','2026-04-01T08:00:00+00:00')]
 result=c.summarise(rows,datetime(2026,4,2,tzinfo=UK))
 assert result['monthlyRealMiles']==10000 and result['targetStreak']==1
 assert result['mostMonthlyJobs']==2 and result['monthlyLabel']=='April 2026'

def test_missing_and_nested_route_metadata_remain_truthful():
 unknown=c.delivery(row(12,'2026-11-01T00:00:00+00:00',payload='bad json'))
 assert unknown['game'] is None and unknown['origin']=='' and unknown['destination']==''
 known=c.delivery(row(12,'2026-11-01T00:00:00+00:00',payload=json.dumps({'job_metadata':{'game':'ATS','origin':{'city':{'name':'Seattle'}},'destination':'Spokane','cargo':'Logs'}})))
 assert (known['game'],known['origin'],known['destination'],known['cargo'])==('ATS','Seattle','Spokane','Logs')

@pytest.mark.asyncio
async def test_private_native_view_paginates_filters_and_restricts_viewer():
 data=c.summarise([row(20,'2026-11-01T00:00:00+00:00',kind='race' if i%2 else 'real') for i in range(12)],datetime(2026,11,28,tzinfo=UK))
 data.update(name='Mystic',driverNumber='001',longestJob=5346,monthlyPosition=2,allTimePosition=2)
 view=career_ui.CareerView(data,42)
 assert view.previous.disabled and not view.next.disabled
 assert len(view.embed().fields[-1].value.split('\n\n'))==5
 view.mode='race';view.sync_buttons();assert len(view.rows())==6
 view.page=1;view.sync_buttons();assert view.next.disabled and not view.previous.disabled
 assert len(view.embed().fields[-1].value.split('\n\n'))==1
 interaction=SimpleNamespace(user=SimpleNamespace(id=99),response=SimpleNamespace(send_message=AsyncMock()))
 assert not await view.interaction_check(interaction)
 interaction.response.send_message.assert_awaited_once()
 assert interaction.response.send_message.call_args.kwargs['ephemeral']


def test_signed_card_request_cannot_be_reused_for_another_driver(monkeypatch):
 monkeypatch.setenv('AT_COMMAND_BRIDGE_SECRET','example-secret-for-unit-tests-only-123456')
 monkeypatch.setenv('AT_COMMAND_BRIDGE_ENABLED','true')
 path='/api/driver-card?driver=42&side=front'
 request=SimpleNamespace(method='GET',path_qs=path,headers=bridge.service_headers('GET',path))
 assert bridge.valid_service_request(request)
 request.path_qs='/api/driver-card?driver=43&side=front'
 assert not bridge.valid_service_request(request)
 request.path_qs=path;request.headers['x-at-timestamp']='1'
 assert not bridge.valid_service_request(request)


def test_appearance_settings_cannot_claim_an_unearned_achievement_or_role():
 tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
 node=next(n for n in tree.body if getattr(n,'name',None)=='validate_career_settings')
 ns=dict(tachograph=tachograph,career_details=c)
 exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),ns)
 member=SimpleNamespace(roles=[SimpleNamespace(name='Driver')]);profile=dict(permanent_achievements=(40000,),race_permanent_achievements=())
 settings=dict(truckKey=tachograph.truck_keys()[0],division='Transport',featuredAchievement=40000,featuredTrack='real',countryCode='GB')
 assert ns['validate_career_settings'](member,profile,settings)[0]
 settings['featuredAchievement']=100000;assert not ns['validate_career_settings'](member,profile,settings)[0]
 settings['featuredAchievement']=40000;settings['featuredTrack']='race';assert not ns['validate_career_settings'](member,profile,settings)[0]
 settings['featuredAchievement']=None;settings['division']='Recovery';assert not ns['validate_career_settings'](member,profile,settings)[0]
 settings['division']='Transport';settings['truckKey']='../../other-user';assert not ns['validate_career_settings'](member,profile,settings)[0]

@pytest.mark.asyncio
async def test_career_projection_serializes_real_records_and_preserves_automatic_selection():
 tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
 node=next(n for n in tree.body if getattr(n,'name',None)=='assemble_hub_career')
 raw=[row('10000.75','2026-11-01T10:00:00+00:00',payload={'job_metadata':{'game':'ATS','origin':'Seattle','destination':'Spokane'}})]
 data=c.summarise(raw,datetime(2026,11,28,tzinfo=UK))
 fake_c=SimpleNamespace(fetch=AsyncMock(return_value={42:data}),division_choices=c.division_choices)
 ns=dict(career_details=fake_c,tachograph=tachograph,GUILD_ID=1,
  fetch_period_rankings=AsyncMock(return_value=[dict(discord_user_id=43,real_miles=20000),dict(discord_user_id=42,real_miles=10000)]),
  leaderboard_period_bounds=lambda period:(None,None),datetime=datetime,timezone=timezone,
  website_company_bridge=SimpleNamespace(cached_hub_link=lambda uid:dict(name='Mystic',url='https://at-transport-ltd.com/drivers-hub/profile/42')))
 import math
 ns['math']=math
 exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),ns)
 member=SimpleNamespace(id=42,display_name='[001] CEO | Mystic',roles=[SimpleNamespace(name='Owner')],guild=SimpleNamespace(id=1))
 profile=dict(real_miles=174023,trucky_race_miles=Decimal('18915.99'),jobs_completed=207,longest_job_distance=5346,permanent_achievements=(40000,100000),race_permanent_achievements=())
 prefs=dict(truck_key=tachograph.truck_keys()[0],country_code='GB',featured_achievement=None)
 result=await ns['assemble_hub_career'](SimpleNamespace(fetchval=AsyncMock(return_value=2)),member,profile,prefs)
 assert result['featuredAutomatic'] is True
 assert result['featuredAchievement']==100000
 assert result['name']=='Mystic' and result['driverNumber']=='001'
 assert result['monthlyPosition']==2 and result['allTimePosition']==2
 assert result['raceRank']['remaining']==1085
 assert result['deliveries'][0]['origin']=='Seattle'
 assert result['accounts'][-1]['provider']=='Drivers Hub'
 assert json.loads(json.dumps(result))['raceMiles']==18915.99
