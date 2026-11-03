import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "text" | "danger";

interface StyleProps {
  variant?: ButtonVariant;
  size?: "medium" | "small";
  /** Square button with only an icon; give it an aria-label. */
  iconOnly?: boolean;
  icon?: ReactNode;
}

function classes({ variant = "secondary", size = "medium", iconOnly }: StyleProps, extra?: string) {
  return [
    styles.button,
    styles[variant],
    size === "small" && styles.small,
    iconOnly && styles.icon,
    extra,
  ]
    .filter(Boolean)
    .join(" ");
}

type ButtonProps = StyleProps & ButtonHTMLAttributes<HTMLButtonElement>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, iconOnly, icon, className, children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={classes({ variant, size, iconOnly }, className)}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
});

type ButtonLinkProps = StyleProps & LinkProps;

/** A router link that looks like a button (navigation, not an action). */
export function ButtonLink({ variant, size, iconOnly, icon, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes({ variant, size, iconOnly }, className)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
