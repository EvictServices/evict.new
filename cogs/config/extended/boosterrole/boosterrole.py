import re
from contextlib import suppress
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Annotated, Optional, cast

from discord import Color, Embed, HTTPException, Member, Message, PartialEmoji, Role
from discord.ext.commands import (
    BadArgument,
    BucketType,
    Cog,
    Range,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

import cogs.config.extended.boosterrole.boosterrole as boosterrole
from utils.tools import CompositeMetaClass, MixinMeta, convert_image, dominant_color
from core.context import Context
from core.database.settings import Settings
from utils.conversions import PartialAttachment, StrictRole
from utils.tools.formatter import plural
from managers.paginator import Paginator

log = getLogger("evict/colors")


def build_colors() -> dict[str, str]:
    """
    Build the dictionary from colors.json
    """

    file = Path(boosterrole.__file__).with_name("colors.json")
    return loads(file.read_bytes())


COLORS = build_colors()


class BoosterRole(MixinMeta, metaclass=CompositeMetaClass):
    """
    Manage your own custom booster roles.
    """

    @Cog.listener("on_member_unboost")
    async def boosterrole_delete_unboost(self, member: Member) -> None:
        """
        Remove the member's booster role if they unboost.
        """

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                DELETE FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                RETURNING role_id
                """,
                member.guild.id,
                member.id,
            ),
        )
        if not role_id:
            return

        role = member.guild.get_role(role_id)
        if not role:
            return

        with suppress(HTTPException):
            await role.delete(
                reason=f"Member no longer boosting. {member} ({member.id})"
            )

    @Cog.listener("on_member_remove")
    async def boosterrole_delete_leave(self, member: Member) -> None:
        """
        Delete the member's booster role if they leave.
        """

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                DELETE FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                RETURNING role_id
                """,
                member.guild.id,
                member.id,
            ),
        )
        if not role_id:
            return

        role = member.guild.get_role(role_id)
        if not role:
            return

        with suppress(HTTPException):
            await role.delete(reason=f"Member left the server. {member} ({member.id})")

    def color_search(self, color: str) -> Color:
        """
        Search for a color in the colors.json file,
        or return the color if it's a hex code.
        """

        color = color.lower().replace("color", "").strip()
        if not color:
            raise BadArgument("You must provide a color!")

        if color in {"black", "nigga"}:
            color = "010101"

        if re.match(r"^(?:[0-9a-fA-F]{3}){1,2}$", color):
            return Color(int(color, 16))

        final = []
        fuzzer = process.extract_iter(
            color, COLORS, scorer=DamerauLevenshtein.normalized_distance
        )
        for res in fuzzer:
            if res[1] == 0:
                final.insert(0, res)
                break
            if res[1] < 1.0:
                final.append(res)

        if not final:
            raise BadArgument(f"Color **{color}** doesn't exist")

        return Color(int([x[2] for x in sorted(final, key=lambda x: x[1])][0], 16))

    def is_allowed(self, member: Member, settings: Settings) -> bool:
        """
        Check if a member is allowed to assign a booster role.
        """

        if member.premium_since:
            return True

        elif member.id in self.bot.owner_ids:
            return True

        elif member.guild_permissions.administrator:
            return True

        return any(role in member.roles for role in settings.booster_role_include)

    @group(
        aliases=["br", "color"],
        invoke_without_command=True,
        example="black",
    )
    async def boosterrole(self, ctx: Context, *, color: Color | str) -> Message:
        """
        Assign yourself a custom color role.
        """

        if not ctx.settings.booster_role_base:
            return await ctx.warn(
                f"The base role has not been set yet! Use the `{ctx.clean_prefix}boosterrole base` command to set it!"
            )

        elif not self.is_allowed(ctx.author, ctx.settings):
            return await ctx.warn("You must be a server booster to use this command!")

        multi_enabled = await self.bot.db.fetchval(
            """
            SELECT multi_boost_enabled
            FROM booster_role
            WHERE guild_id = $1
            """,
            ctx.guild.id
        )

        if isinstance(color, str):
            color = self.color_search(color)

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        role_ids = await self.bot.db.fetch(
            """
            SELECT role_id
            FROM booster_role
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            ctx.author.id,
        )

        if role_ids and not multi_enabled:
            role = ctx.guild.get_role(role_ids[0]['role_id'])
            if role:
                await role.edit(color=color, reason=reason)
                if role not in ctx.author.roles:
                    await ctx.author.add_roles(role, reason=reason)
                return await ctx.neutral(f"Successfully set your color to `{color}`", color=color)

        elif role_ids and multi_enabled:
            boost_count = await self.bot.db.fetchval(
                """
                SELECT boost_count
                FROM boost_history
                WHERE guild_id = $1 AND user_id = $2
                """,
                ctx.guild.id,
                ctx.author.id
            ) or 0

            if boost_count >= 2:
                if len(role_ids) >= 2:
                    return await ctx.warn(
                        f"You already have 2 booster roles! Use `{ctx.clean_prefix}boosterrole organize` to manage them."
                    )

        name = ctx.author.display_name.lower()
        role = await ctx.guild.create_role(
            name=name,
            color=color,
            reason=reason,
        )

        await self.bot.db.execute(
            """
            INSERT INTO booster_role
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET role_id = EXCLUDED.role_id
            """,
            ctx.guild.id,
            ctx.author.id,
            role.id,
        )
        await ctx.guild.edit_role_positions(
            positions={
                role: ctx.settings.booster_role_base.position - 1,
            },
        )

        if role not in ctx.author.roles:
            await ctx.author.add_roles(role, reason=reason)

        return await ctx.neutral(f"Successfully set your color to `{color}`", color=color)

    @boosterrole.command(name="organize", aliases=["order"])
    async def boosterrole_organize(self, ctx: Context, top_role: Role) -> Message:
        """
        Organize your booster roles by specifying which should be on top.
        Only works if you have multiple booster roles enabled and 2+ boosts.
        """
        role_ids = await self.bot.db.fetch(
            """
            SELECT role_id
            FROM booster_role
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            ctx.author.id,
        )

        if len(role_ids) < 2:
            return await ctx.warn("You need to have two booster roles to organize them!")

        roles = [ctx.guild.get_role(r['role_id']) for r in role_ids]
        if top_role.id not in [r.id for r in roles]:
            return await ctx.warn("That's not one of your booster roles!")

        bottom_role = next(r for r in roles if r.id != top_role.id)

        await ctx.guild.edit_role_positions({
            top_role: ctx.settings.booster_role_base.position - 1,
            bottom_role: ctx.settings.booster_role_base.position - 2
        })

        return await ctx.approve(f"Successfully organized your booster roles with {top_role.mention} on top!")

    @boosterrole.group(name="share", example="@x", invoke_without_command=True)
    async def boosterrole_share(self, ctx: Context, *, member: Optional[Member] = None) -> Message:
        """
        Share your booster role with another member.
        """

        if not member:
            return await ctx.send_help(ctx.command)

        # settings = await self.bot.db.fetchrow(
        #     f"SELECT shared FROM booster_role WHERE guild_id = $1", 
        #     ctx.guild.id
        # )

        # if not settings:
        #     return await ctx.warn("Booster role sharing is disabled in this server!")

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT role_id
                FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                """,
                ctx.guild.id,
                ctx.author.id,
            ),
        )
        if not role_id:
            return await ctx.warn("You don't have a booster role!")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.warn("Your booster role no longer exists!")
        
        if member.bot:
            return await ctx.warn("You cannot share your booster role with a bot!")
        
        if role in member.roles:
            await ctx.prompt("Are you sure you want to remove your booster role from this member?")
            await member.remove_roles(
                role,
                reason=f"Booster role removed by {ctx.author} ({ctx.author.id})",
            )
            return await ctx.approve(f"Removed your booster role from {member.mention}!")
        
        await ctx.confirm(f"{member.mention} do you accept the booster role {role.mention} from {ctx.author.mention}?", user=member)
    
        await member.add_roles(
                role,
                reason=f"Booster role shared by {ctx.author} ({ctx.author.id})",
            )
        
        return await ctx.approve(f"Shared your booster role with {member.mention}")

    @boosterrole_share.command(name="remove", example="@x")
    async def boosterrole_share_remove(self, ctx: Context, role: Role) -> Message:
        """
        Remove a shared booster role from yourself.
        """

        if role not in ctx.author.roles:
            return await ctx.warn("You don't have this role!")
        
        check = await self.bot.db.fetchrow("SELECT role_id FROM booster_role WHERE role_id = $1", role.id)
        if not check:
            return await ctx.warn("This role is not a booster role!")
        
        check1 = await self.bot.db.fetchrow("SELECT * FROM booster_role WHERE role_id = $1 AND user_id = $2", role.id, ctx.author.id)
        if check1:
            return await ctx.warn(f"You cannot remove a shared booster role you own! Run ``{ctx.clean_prefix}boosterrole delete`` instead.")
        
        await ctx.prompt(f"Are you sure you want to remove the {role.mention} role?")
        await ctx.author.remove_roles(
            role,
            reason=f"Booster role removed by {ctx.author} ({ctx.author.id})",
        )
        await ctx.approve(f"Removed the shared booster role {role.mention} from you!")

    # @has_permissions(manage_guild=True)
    # @boosterrole_share.command(name="enable")
    # async def boosterrole_share_enable(self, ctx: Context) -> Message:
    #     """
    #     Enable sharing of booster roles with others.
    #     """

    #     shared = await self.bot.db.execute(
    #         """
    #         UPDATE booster_role
    #         SET shared = TRUE
    #         WHERE guild_id = $1
    #         """,
    #         ctx.guild.id,
    #     )

    #     if shared:
    #         return await ctx.warn("Booster role sharing is already enabled in this server!")

    #     return await ctx.approve("Successfully enabled sharing of booster roles in this server!")
    
    # @has_permissions(manage_guild=True)
    # @boosterrole_share.command(name="disable")
    # async def boosterrole_share_disable(self, ctx: Context) -> Message:
    #     """
    #     Disable sharing of booster roles with others.
    #     """

    #     shared = await self.bot.db.execute(
    #         """
    #         DELETE FROM booster_role
    #         WHERE guild_id = $1
    #         AND shared = TRUE
    #         """,
    #         ctx.guild.id,
    #     )

    #     if not shared == 0:
    #         return await ctx.warn("Booster role sharing is already disabled in this server!")

    #     return await ctx.approve("Successfully disabled sharing of booster roles in this server!")

    @boosterrole.command(
        name="remove",
        aliases=["delete", "del", "rm"],
    )
    async def boosterrole_remove(self, ctx: Context) -> Message:
        """
        Remove your custom role.
        """

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT role_id
                FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                """,
                ctx.guild.id,
                ctx.author.id,
            ),
        )
        if not role_id:
            return await ctx.warn("You don't have a booster role!")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.warn("Your booster role no longer exists!")

        await self.bot.db.execute(
            """
            DELETE FROM booster_role
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            ctx.author.id,
        )

        await role.delete(reason=f"Booster role for {ctx.author} ({ctx.author.id})")
        return await ctx.approve("Successfully  removed your booster role")

    @boosterrole.command(
        name="dominant",
        aliases=["pfp", "avatar"],
    )
    async def boosterrole_dominant(
        self,
        ctx: Context,
        *,
        member: Member = parameter(
            default=lambda ctx: ctx.author,
        ),
    ) -> Message:
        """
        Set your color to your avatar's dominant color.
        """

        member = member or ctx.author

        async with ctx.typing():
            buffer = await member.display_avatar.read()
            color = await dominant_color(buffer)

        return await ctx.invoke(self.boosterrole, color=color)

    @boosterrole.command(
        name="rename",
        aliases=["name"],
        example="x",
    )
    async def boosterrole_rename(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """
        Rename your booster role.
        """

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT role_id
                FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                """,
                ctx.guild.id,
                ctx.author.id,
            ),
        )
        if not role_id:
            return await ctx.warn("You don't have a booster role!")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.warn("Your booster role no longer exists!")

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        await role.edit(name=name, reason=reason)
        return await ctx.approve(f"Changed your booster role's name to **{name}**")

    @boosterrole.command(name="icon", example="https://example.com/image.png")
    async def boosterrole_icon(
        self,
        ctx: Context,
        icon: PartialEmoji | PartialAttachment | str = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """
        Change the icon of your booster role.
        """

        if ctx.guild.premium_tier < 2:
            return await ctx.warn(
                "Role icons are only available for **level 2** boosted servers!"
            )

        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT role_id
                FROM booster_role
                WHERE guild_id = $1
                AND user_id = $2
                """,
                ctx.guild.id,
                ctx.author.id,
            ),
        )
        if not role_id:
            return await ctx.warn("You don't have a booster role!")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.warn("Your booster role no longer exists!")

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        if isinstance(icon, str) and icon in ("none", "remove", "delete"):
            if not role.display_icon:
                return await ctx.warn("Your booster role doesn't have an icon!")

            await role.edit(display_icon=None, reason=reason)
            return await ctx.approve("Removed your booster role's icon")

        buffer: bytes | str
        processing: Optional[Message] = None

        if isinstance(icon, str):
            buffer = icon
        elif isinstance(icon, PartialEmoji):
            buffer = await icon.read()
            if icon.animated:
                processing = await ctx.neutral(
                    "Converting animated emoji to a static image..."
                )
                buffer = await convert_image(buffer, "png")

        elif icon.is_gif():
            processing = await ctx.neutral("Converting GIF to a static image...")
            buffer = await convert_image(icon.buffer, "png")

        elif not icon.is_image():
            return await ctx.warn("The attachment must be an image!")

        else:
            buffer = icon.buffer

        if processing:
            await processing.delete(delay=0.5)

        await role.edit(
            display_icon=buffer,
            reason=reason,
        )
        return await ctx.approve(
            "Changed your booster role's icon to "
            + (
                f"[**image**]({icon.url})"
                if isinstance(icon, PartialAttachment)
                else f"**{icon}**"
            )
        )

    @boosterrole.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_list(self, ctx: Context) -> Message:
        """
        View all booster roles.
        """

        roles = [
            f"{role.mention} (`{role.id}`) - {member.mention}"
            for record in await self.bot.db.fetch(
                """
                SELECT user_id, role_id
                FROM booster_role
                WHERE guild_id = $1
                """,
                ctx.guild.id,
            )
            if (role := ctx.guild.get_role(record["role_id"]))
            and (member := ctx.guild.get_member(record["user_id"]))
        ]
        if not roles:
            return await ctx.warn("No booster roles exist for this server!")

        paginator = Paginator(
            ctx,
            entries=roles,
            embed=Embed(
                title="Booster Roles",
            ),
        )
        return await paginator.start()

    @boosterrole.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    @has_permissions(manage_roles=True)
    @max_concurrency(1, BucketType.guild)
    async def boosterrole_clear(self, ctx: Context) -> Message:
        """
        Delete all booster roles.
        """

        await ctx.prompt(
            "Are you sure you want to delete all booster roles?",
        )

        async with ctx.typing():
            roles = [
                role
                for record in await self.bot.db.fetch(
                    """
                    DELETE FROM booster_role
                    WHERE guild_id = $1
                    RETURNING role_id
                    """,
                    ctx.guild.id,
                )
                if (role := ctx.guild.get_role(record["role_id"]))
            ]
            if not roles:
                return await ctx.warn("No booster roles exist for this server!")

            for role in roles:
                with suppress(HTTPException):
                    await role.delete(
                        reason=f"Booster roles cleared by {ctx.author} ({ctx.author.id})"
                    )

        return await ctx.approve(
            f"Successfully  deleted {plural(len(roles), md='`'):booster role}"
        )

    @boosterrole.command(
        name="base",
        aliases=["set"],
        example="@base"
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_base(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(
                check_integrated=False,
            ),
        ],
    ) -> Message:
        """
        Set the base position for booster roles.
        """

        await ctx.settings.update(booster_role_base_id=role.id)
        return await ctx.approve(f"Booster roles will now be made under {role.mention}")

    @boosterrole.group(
        name="include",
        aliases=["allow"],
        invoke_without_command=True,
        example="@mod",
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_include(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole,
        ],
    ) -> Message:
        """
        Allow a role to bypass the booster requirement.
        """

        if role in ctx.settings.booster_role_include:
            return await ctx.warn(
                f"Already allowing {role.mention} to bypass the booster requirement!",
            )

        ctx.settings.booster_role_include_ids.append(role.id)
        await ctx.settings.update()
        return await ctx.approve(
            f"Now allowing {role.mention} to bypass the booster requirement"
        )

    @boosterrole_include.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        example="@mod",
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_include_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole,
        ],
    ) -> Message:
        """
        Remove a role from the booster role include list.
        """

        if role not in ctx.settings.booster_role_include:
            return await ctx.warn(
                f"Already not allowing {role.mention} to bypass the booster requirement!",
            )

        ctx.settings.booster_role_include_ids.remove(role.id)
        await ctx.settings.update()
        return await ctx.approve(
            f"No longer allowing {role.mention} to bypass the booster requirement"
        )

    @boosterrole_include.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_include_list(self, ctx: Context) -> Message:
        """
        View all roles that bypass the booster requirement.
        """

        if not ctx.settings.booster_role_include:
            return await ctx.warn("No roles bypass the booster requirement!")

        paginator = Paginator(
            ctx,
            entries=[
                f"{role.mention} (`{role.id}`)"
                for role in ctx.settings.booster_role_include
            ],
            embed=Embed(
                title="Allowed Roles",
            ),
        )
        return await paginator.start()

    @Cog.listener("on_member_update")
    async def track_boosts(self, before: Member, after: Member) -> None:
        """Track when members boost the server"""
        if before.premium_since == after.premium_since:
            return
            
        if after.premium_since and (not before.premium_since):  
            await self.bot.db.execute(
                """
                INSERT INTO boost_history (guild_id, user_id, boost_count)
                VALUES ($1, $2, 1)
                ON CONFLICT (guild_id, user_id) 
                DO UPDATE SET 
                    boost_count = boost_history.boost_count + 1,
                    last_boost_date = CURRENT_TIMESTAMP
                """,
                after.guild.id,
                after.id
            )

    @boosterrole.command(name="multiboost", aliases=["multi"])
    @has_permissions(manage_guild=True)
    async def boosterrole_multiboost(self, ctx: Context) -> Message:
        """
        Toggle whether users can have multiple booster roles when boosting multiple times.
        Users need at least 2 active boosts to be eligible.
        """
        
        current = await self.bot.db.fetchval(
            """
            SELECT multi_boost_enabled
            FROM booster_role
            WHERE guild_id = $1
            """,
            ctx.guild.id
        )

        new_state = not current if current is not None else True
        await self.bot.db.execute(
            """
            UPDATE booster_role
            SET multi_boost_enabled = $1
            WHERE guild_id = $2
            """,
            new_state,
            ctx.guild.id
        )

        return await ctx.approve(
            f"Multiple booster roles are now {'enabled' if new_state else 'disabled'} "
            f"for users with 2+ boosts"
        )
