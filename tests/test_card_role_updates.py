import ast
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import AsyncMock
import pytest
import tachograph

@pytest.mark.parametrize('name,expected',[
 ('ʟᴇᴀᴅ ɪᴛ ꜱᴜᴘᴘᴏʀᴛ / ᴅʀɪᴠᴇʀ','LEAD IT SUPPORT / DRIVER'),
 ('Recruitment Manager','RECRUITMENT MANAGER / DRIVER'),
 ('Events Manager','EVENTS MANAGER / DRIVER'),
 ('Lead IT Server Support','LEAD IT SERVER SUPPORT / DRIVER'),
 ('Lead IT Server Support/Driver','LEAD IT SERVER SUPPORT / DRIVER'),
 ('Owner | Chief Executive Officer','OWNER / DRIVER'),
 ('Co-Owner | Chief Operator Officer','CO-OWNER / DRIVER'),
 ('Driver Dispatcher','DISPATCHER / DRIVER'),
 ('Horizon Wolf','DRIVER'),
 ('','DRIVER'),
 ('Night Shift Operations Manager','NIGHT SHIFT MANAGER / DRIVER'),
 ('Management','MANAGEMENT / DRIVER'),
 ('LEADERSHIP & STAFF','DRIVER'),
])
def test_company_titles_and_decorative_dividers(name,expected):
    assert tachograph.company_role(N(id=42,guild=N(owner_id=1),roles=[N(name=name)]))==expected

@pytest.mark.asyncio
async def test_role_changes_trigger_card_refresh_but_mileage_roles_do_not():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    fn=next(n for n in tree.body if getattr(n,'name',None)=='on_member_update')
    fn.decorator_list=[]
    refresh=AsyncMock()
    ns=dict(os=N(getenv=lambda *_:None),GUILD_ID=1,db_pool=object(),tachograph=tachograph,
        refresh_member_role_cards=refresh,sync_member_role_category_dividers=AsyncMock())
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
    def member(roles): return N(id=42,guild=N(id=1,owner_id=1),roles=[N(id=i,name=n) for i,n in roles],premium_since=None)
    await ns['on_member_update'](member([(1,'Driver')]),member([(1,'Driver'),(2,'Management')]))
    refresh.assert_awaited_once()
    refresh.reset_mock()
    await ns['on_member_update'](member([(1,'Driver')]),member([(1,'Driver'),(3,'Horizon Wolf')]))
    refresh.assert_not_awaited()
