"""Comandos operacionais de identidade.

O Notely não tem tela de cadastro: as contas são criadas por linha de comando. Quando a
senha não é informada, uma é gerada e impressa uma única vez.

    python -m notely.cli bootstrap --email dono@exemplo.com
    python -m notely.cli create-user --email pessoa@exemplo.com
    python -m notely.cli set-password --email dono@exemplo.com
    python -m notely.cli list-users
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.core.auth import AuthError, AuthService
from notely.core.identity import IdentityError
from notely.core.security import SecurityError
from notely.db.identity import SqlAlchemyIdentityStore

DEFAULT_DATABASE_URL = "postgresql+asyncpg://notely:notely@localhost:5432/notely"
GENERATED_PASSWORD_BYTES = 16


def generate_password() -> str:
    return secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Comandos de identidade do Notely")
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser(
        "bootstrap",
        help="define as credenciais do dono, adotando o dono provisório da migration",
    )
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--password", default=None)

    create = commands.add_parser("create-user", help="cria uma conta")
    create.add_argument("--email", required=True)
    create.add_argument("--password", default=None)

    update = commands.add_parser("set-password", help="troca a senha de uma conta existente")
    update.add_argument("--email", required=True)
    update.add_argument("--password", default=None)

    commands.add_parser("list-users", help="lista as contas")
    return parser


async def run(arguments: argparse.Namespace) -> int:
    database_url = os.environ.get("NOTELY_DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = AuthService(lambda: SqlAlchemyIdentityStore(sessions))
    try:
        if arguments.command == "list-users":
            for user in await service.list_users():
                state = "MFA ativo" if user.totp_enabled else "sem MFA"
                password = "com senha" if user.password_hash else "sem senha"
                print(f"{user.normalized_email}\t{password}\t{state}")
            return 0

        password = arguments.password or generate_password()
        if arguments.command == "bootstrap":
            user = await service.set_credentials(email=arguments.email, password=password)
        elif arguments.command == "create-user":
            user = await service.create_user(email=arguments.email, password=password)
        else:
            user = await service.set_password(email=arguments.email, password=password)

        print(f"Conta: {user.email}")
        if arguments.password is None:
            print(f"Senha gerada (guarde agora, não será exibida de novo): {password}")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        return asyncio.run(run(arguments))
    except (AuthError, IdentityError, SecurityError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
