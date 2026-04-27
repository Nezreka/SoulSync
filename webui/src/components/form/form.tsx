import {
  forwardRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';

import styles from './form.module.css';

function mergeClassNames(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ');
}

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
    <div className={mergeClassNames(styles.field, className)}>
      <div className={styles.fieldHeader}>
        {htmlFor ? (
          <label className={styles.fieldLabel} htmlFor={htmlFor}>
            {label}
          </label>
        ) : (
          <div className={styles.fieldLabel}>{label}</div>
        )}
        {helperText ? <div className={styles.fieldHelper}>{helperText}</div> : null}
      </div>
      <div className={styles.fieldControl}>{children}</div>
      {error ? <FormError message={error} /> : null}
    </div>
  );
}

export type TextInputProps = InputHTMLAttributes<HTMLInputElement>;

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={mergeClassNames(styles.textInput, className)} {...props} />;
});

export type TextAreaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={mergeClassNames(styles.textArea, className)} {...props} />;
});

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={mergeClassNames(styles.select, className)} {...props} />;
});

export function OptionCardGroup({
  className,
  children,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={mergeClassNames(styles.optionCardGroup, className)}>{children}</div>;
}

export interface OptionCardProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'title'> {
  description?: ReactNode;
  icon?: ReactNode;
  selected?: boolean;
  title?: ReactNode;
}

export const OptionCard = forwardRef<HTMLButtonElement, OptionCardProps>(function OptionCard(
  { className, children, description, icon, selected = false, title, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      aria-pressed={selected}
      className={mergeClassNames(
        styles.optionCard,
        selected && styles.optionCardSelected,
        className,
      )}
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
    </button>
  );
});

export function OptionButtonGroup({
  className,
  children,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={mergeClassNames(styles.optionButtonGroup, className)}>{children}</div>;
}

export interface OptionButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean;
}

export const OptionButton = forwardRef<HTMLButtonElement, OptionButtonProps>(function OptionButton(
  { className, children, selected = false, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      aria-pressed={selected}
      className={mergeClassNames(
        styles.optionButton,
        selected && styles.optionButtonSelected,
        className,
      )}
      type={type}
      {...props}
    >
      {children}
    </button>
  );
});

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={mergeClassNames(styles.button, className)}
      type={type}
      {...props}
    />
  );
});

export function FormError({ className, message }: { className?: string; message?: ReactNode }) {
  if (!message) return null;

  return (
    <div className={mergeClassNames(styles.formError, className)} role="alert">
      {message}
    </div>
  );
}

export function FormActions({ className, children }: { children: ReactNode; className?: string }) {
  return <div className={mergeClassNames(styles.formActions, className)}>{children}</div>;
}
