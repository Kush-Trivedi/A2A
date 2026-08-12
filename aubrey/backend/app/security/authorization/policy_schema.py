from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

class AuthorizationPolicySchema:
    @classmethod
    async def normalize(cls, engine: AsyncEngine) -> None:
        statement = text(
            """
            UPDATE casbin_rule
            SET v4 = COALESCE(NULLIF(v4, ''), 'allow'),
                v5 = COALESCE(NULLIF(v5, ''), '*')
            WHERE ptype = 'p'
                AND (v4 IS NULL OR v4 = '' OR v5 IS NULL OR v5 = '')
            """
        )
        async with engine.begin() as conn:
            await conn.execute(statement)