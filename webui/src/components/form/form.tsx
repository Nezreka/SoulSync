import { Button as BaseButton } from '@base-ui/react/button';
import { Field } from '@base-ui/react/field';
import { Input as BaseInput } from '@base-ui/react/input';
import { Toggle as BaseToggle } from '@base-ui/react/toggle';
import clsx from 'clsx';
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ButtonHTMLAttributes,
  type SelectHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';

import styles from './form.module.css';

export interface FormFieldProps {
  children: ReactNode;
  className?: string;
  error?: ReactNode;
  helperText?: ReactNode;
  htmlFor?: string;
  label: ReactNode;
}

export function FormField({
  children,
  className,
  error,
  helperText,
  htmlFor,
  label,
}: FormFieldProps) {
  return (
    <Field.Root className={clsx(styles.field, className)}>
      <div className={styles.fieldHeader}>
        {htmlFor ? (
          <label className={styles.fieldLabel} htmlFor={htmlFor}>
            {label}
          </label>
        ) : (
          <Field.Label className={styles.fieldLabel}>{label}</Field.Label>
        )}
        {helperText ? (
          <Field.Description className={styles.fieldHelper}>{helperText}</Field.Description>
        ) : null}
      </div>
      <div className={styles.fieldControl}>{children}</div>
      {error ? <FormError message={error} /> : null}
    </Field.Root>
  );
}

type BaseInputProps = ComponentPropsWithoutRef<typeof BaseInput>;

export type TextInputProps = Omit<BaseInputProps, 'className'> & {
  className?: string;
};

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { className, ...props },
  ref,
) {
  return <BaseInput ref={ref} className={clsx(styles.textInput, className)} {...props} />;
});

export type TextAreaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={clsx(styles.textArea, className)} {...props} />;
});

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={clsx(styles.select, className)} {...props} />;
});

export function OptionCardGroup({
  className,
  children,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={clsx(styles.optionCardGroup, className)}>{children}</div>;
}

export interface OptionCardProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'title' | 'value'
> {
  className?: string;
  description?: ReactNode;
  icon?: ReactNode;
  selected?: boolean;
  title?: ReactNode;
  value?: string;
}

export const OptionCard = forwardRef<HTMLButtonElement, OptionCardProps>(function OptionCard(
  { className, children, description, icon, selected = false, title, type = 'button', ...props },
  ref,
) {
  return (
    <BaseToggle
      ref={ref}
      pressed={selected}
      className={clsx(styles.optionCard, selected && styles.optionCardSelected, className)}
      type={type}
      {...props}
    >
      {children ?? (
        <>
          {icon ? <div className={styles.optionCardIcon}>{icon}</div> : null}
          <div className={styles.optionCardBody}>
            {title ? <div className={styles.optionCardTitle}>{title}</div> : null}
            {description ? <div className={styles.optionCardDescription}>{description}</div> : null}
          </div>
        </>
      )}
    </BaseToggle>
  );
});

export function OptionButtonGroup({
  className,
  children,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={clsx(styles.optionButtonGroup, className)}>{children}</div>;
}

export interface OptionButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'value'> {
  className?: string;
  selected?: boolean;
  value?: string;
}

export const OptionButton = forwardRef<HTMLButtonElement, OptionButtonProps>(function OptionButton(
  { className, children, selected = false, type = 'button', ...props },
  ref,
) {
  return (
    <BaseToggle
      ref={ref}
      pressed={selected}
      className={clsx(styles.optionButton, selected && styles.optionButtonSelected, className)}
      type={type}
      {...props}
    >
      {children}
    </BaseToggle>
  );
});

type BaseButtonProps = ComponentPropsWithoutRef<typeof BaseButton>;

export type ButtonProps = Omit<BaseButtonProps, 'className'> & {
  className?: string;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, type = 'button', ...props },
  ref,
) {
  return <BaseButton ref={ref} className={clsx(styles.button, className)} type={type} {...props} />;
});

export function FormError({ className, message }: { className?: string; message?: ReactNode }) {
  if (!message) return null;

  return (
    <div className={clsx(styles.formError, className)} role="alert">
      {message}
    </div>
  );
}

export function FormActions({ className, children }: { children: ReactNode; className?: string }) {
  return <div className={clsx(styles.formActions, className)}>{children}</div>;
}
