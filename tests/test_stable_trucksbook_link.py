import asyncio
import unittest
from types import SimpleNamespace
from test_wotr_hotfix import NS, Embed, load_function

class StableLinkTests(unittest.TestCase):
    def test_profile_id_url_validation(self):
        parse = NS['trucksbook_author_user_id']
        for url in ('https://trucksbook.eu/profile/12345', 'https://trucksbook.eu/profile/570597', 'https://trucksbook.eu/user/12345', 'https://www.trucksbook.eu/user/12345/', 'https://trucksbook.eu/user/12345?lang=en'):
            self.assertEqual(parse({'author': {'url': url}}), 570597 if '570597' in url else 12345)
        for url in ('https://evil.test/user/12345', 'https://trucksbook.eu.evil.test/user/12345', 'https://trucksbook.eu/user/0', 'https://trucksbook.eu/user/12345oops', 'https://trucksbook.eu/user/99999999999999999999999999', ''):
            self.assertIsNone(parse({'author': {'url': url}}))

    def test_id_precedes_changed_or_colliding_display_name(self):
        async def run():
            queries = []
            class Connection:
                async def fetchval(self, sql, value):
                    queries.append((sql, value))
                    if 'trucksbook_user_id = $1' in sql:
                        return {100: 42, 200: 43}.get(value)
                    return 999
                async def fetch(self, sql, name):
                    if name == 'Mystical Custom': return [{'discord_user_id': 613036707863986236, 'trucksbook_user_id': None}]
                    if name == 'same name': return [{'discord_user_id': 999, 'trucksbook_user_id': 400}]
                    return [{'discord_user_id': 999, 'trucksbook_user_id': None}]
            class Lease:
                async def __aenter__(self): return Connection()
                async def __aexit__(self, *args): pass
            ns = dict(NS, db_pool=SimpleNamespace(acquire=Lease), DRIVER_MAPPINGS={})
            load_function('normalise_trucksbook_name', ns)
            find = load_function('find_driver_link', ns)
            self.assertEqual(await find('Trucker_Nate', 100), 42)
            self.assertEqual(await find('Mystic3399', 200), 43)
            self.assertEqual(await find('same name', 100), 42)
            self.assertIsNone(await find('same name', 300))
            self.assertTrue(all('trucksbook_user_id = $1' in sql for sql, _ in queries))
            self.assertEqual(await find('Legacy Name'), 999)
            self.assertEqual(await find('Legacy Name', 777), 999)
            self.assertEqual(await find('Mystic3399', 777), 613036707863986236)
        asyncio.run(run())

    def test_live_job_uses_author_id(self):
        async def run():
            looked_up = []
            credited = []
            async def find(name, provider_id):
                looked_up.append((name, provider_id))
                return 42 if provider_id == 100 else None
            async def record(**kwargs):
                credited.append(kwargs)
                return {'status': 'duplicate'}
            async def noop(*args): pass
            ns = dict(NS, GUILD_ID=1, find_driver_link=find, record_real_job=record,
                      bot=SimpleNamespace(get_guild=lambda _: None, process_commands=noop))
            handler = load_function('on_message', ns)
            e = Embed('68100778', distance=348)
            e.data['author'] = {'name': 'Trucker_Nate', 'url': 'https://trucksbook.eu/profile/100'}
            await handler(SimpleNamespace(webhook_id=9, embeds=[e]))
            self.assertEqual(looked_up, [('Trucker_Nate', 100)])
            self.assertEqual(credited[0]['accepted_distance'], 348)
            self.assertEqual(credited[0]['discord_user_id'], 42)
        asyncio.run(run())

class DividerPermissionTests(unittest.TestCase):
    def test_unmanageable_members_are_skipped_honestly(self):
        async def run():
            ns = dict(NS, GUILD_ID=1, ROLE_CATEGORY_DIVIDERS={})
            sync = load_function('sync_member_role_category_dividers', ns)
            bot = SimpleNamespace(top_role=10, guild_permissions=SimpleNamespace(manage_roles=True))
            guild = SimpleNamespace(id=1, owner_id=42, me=bot, roles=[])
            for user_id, role in [(42, 1), (43, 20)]:
                result = await sync(SimpleNamespace(id=user_id, guild=guild, top_role=role))
                self.assertEqual(result['skipped'], 1)
                self.assertEqual(result['added'], 0)
            bot.guild_permissions.manage_roles = False
            result = await sync(SimpleNamespace(id=44, guild=guild, top_role=1))
            self.assertEqual(result['skipped'], 1)
        asyncio.run(run())

if __name__ == '__main__': unittest.main()
