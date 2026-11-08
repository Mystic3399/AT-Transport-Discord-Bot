from steam_profile_names import parse_profile_name


def test_steam_name_must_belong_to_verified_id():
    xml=b'<profile><steamID64>76561198271926091</steamID64><steamID><![CDATA[Mystic & A&T]]></steamID></profile>'
    assert parse_profile_name(xml,'76561198271926091')=='Mystic & A&T'
    assert parse_profile_name(xml,'76561198271926092') is None
    assert parse_profile_name(b'<profile><error>private</error></profile>','76561198271926091') is None
    assert parse_profile_name(b'<!DOCTYPE profile><profile/>','76561198271926091') is None
