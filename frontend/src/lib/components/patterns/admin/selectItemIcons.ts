import {
  CalendarDays,
  Coins,
  CreditCard,
  Crown,
  Flame,
  Gift,
  Megaphone,
  Moon,
  Shield,
  ShieldCheck,
  Snowflake,
  Sun,
  Tag,
  Ticket,
  UserRound,
  Users,
  UsersRound,
  Zap,
} from "$components/ui/icons.js";

/**
 * Icons a select item may request by name.
 *
 * The name is a neutral string so a backend descriptor — including one owned by
 * an extension — can pick a glyph without shipping a component. An unknown name
 * renders nothing, which keeps an older admin bundle compatible with a newer
 * descriptor.
 */
export const SELECT_ITEM_ICONS: Record<string, unknown> = {
  "calendar-days": CalendarDays,
  coins: Coins,
  "credit-card": CreditCard,
  crown: Crown,
  flame: Flame,
  gift: Gift,
  megaphone: Megaphone,
  moon: Moon,
  shield: Shield,
  "shield-check": ShieldCheck,
  snowflake: Snowflake,
  sun: Sun,
  tag: Tag,
  ticket: Ticket,
  user: UserRound,
  users: Users,
  "users-round": UsersRound,
  zap: Zap,
};

export type SelectItemIcon = keyof typeof SELECT_ITEM_ICONS;
