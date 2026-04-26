import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import {
  Button,
  FormActions,
  FormError,
  FormField,
  OptionButton,
  OptionButtonGroup,
  OptionCard,
  OptionCardGroup,
  Select,
  TextArea,
  TextInput,
} from './form';

function FormDemo() {
  const [title, setTitle] = useState('');
  const [details, setDetails] = useState('');
  const [category, setCategory] = useState<'wrong_cover' | 'wrong_metadata'>('wrong_cover');
  const [priority, setPriority] = useState<'low' | 'normal' | 'high'>('normal');
  const [status, setStatus] = useState('open');

  return (
    <form>
      <FormField label="Title" helperText="Short summary" htmlFor="title-input">
        <TextInput
          id="title-input"
          placeholder="Enter title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </FormField>

      <FormField label="Details" helperText="Longer explanation" htmlFor="details-input">
        <TextArea
          id="details-input"
          placeholder="Enter details"
          rows={3}
          value={details}
          onChange={(event) => setDetails(event.target.value)}
        />
      </FormField>

      <FormField label="Category" helperText="Pick one">
        <OptionCardGroup>
          <OptionCard
            description="Album art is wrong"
            icon="🖼️"
            onClick={() => setCategory('wrong_cover')}
            selected={category === 'wrong_cover'}
            title="Wrong Cover"
          />
          <OptionCard
            description="Metadata needs fixing"
            icon="🏷️"
            onClick={() => setCategory('wrong_metadata')}
            selected={category === 'wrong_metadata'}
            title="Wrong Metadata"
          />
        </OptionCardGroup>
      </FormField>

      <FormField label="Priority" helperText="Set urgency">
        <OptionButtonGroup>
          {(['low', 'normal', 'high'] as const).map((value) => (
            <OptionButton key={value} onClick={() => setPriority(value)} selected={priority === value}>
              {value[0].toUpperCase()}
              {value.slice(1)}
            </OptionButton>
          ))}
        </OptionButtonGroup>
      </FormField>

      <FormField label="Status" helperText="Shared select primitive" htmlFor="status-select">
        <Select
          id="status-select"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="resolved">Resolved</option>
        </Select>
      </FormField>

      <FormError message="Validation failed" />

      <FormActions>
        <Button type="button">Cancel</Button>
        <Button type="submit">Save</Button>
      </FormActions>
    </form>
  );
}

describe('form primitives', () => {
  it('render accessible controls and support selection state', () => {
    render(<FormDemo />);

    expect(screen.getByLabelText('Title')).toHaveAttribute('placeholder', 'Enter title');
    expect(screen.getByLabelText('Details')).toHaveAttribute('placeholder', 'Enter details');
    expect(screen.getByText('Short summary')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Validation failed');
    expect(screen.getByLabelText('Status')).toHaveValue('open');
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'resolved' } });
    expect(screen.getByLabelText('Status')).toHaveValue('resolved');

    const wrongCover = screen.getByRole('button', { name: /wrong cover/i });
    const wrongMetadata = screen.getByRole('button', { name: /wrong metadata/i });
    expect(wrongCover).toHaveAttribute('aria-pressed', 'true');
    expect(wrongMetadata).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(wrongMetadata);
    expect(wrongCover).toHaveAttribute('aria-pressed', 'false');
    expect(wrongMetadata).toHaveAttribute('aria-pressed', 'true');

    const highPriority = screen.getByRole('button', { name: 'High' });
    expect(highPriority).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(highPriority);
    expect(highPriority).toHaveAttribute('aria-pressed', 'true');

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });
});
