import React, { useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Input,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { ArrowDown, ArrowUp, Plus, SquareMinus } from 'lucide-react';
import API from '../../api.js';
import {
  MAX_BACKUP_SERVER_URLS,
  cleanBackupUrls,
  moveItem,
  serverLabel,
} from '../../utils/forms/M3uUtils.js';

const ResultBadge = ({ result }) => {
  if (!result) return null;
  if (result.pending) {
    return (
      <Badge size="sm" variant="light" color="gray">
        Testing…
      </Badge>
    );
  }
  if (!result.success) {
    return (
      <Tooltip label={result.error || 'Failed'}>
        <Badge size="sm" variant="light" color="red">
          {result.error || 'Failed'}
        </Badge>
      </Tooltip>
    );
  }
  const ms = result.response_time_ms ?? 0;
  const color = ms < 1000 ? 'green' : ms < 3000 ? 'yellow' : 'orange';
  return (
    <Badge size="sm" variant="light" color={color}>
      {ms} ms
    </Badge>
  );
};

/**
 * Editor for an M3U account's backup server URLs, with a reachability test.
 *
 * getTestValues() returns the current primary URL and credentials from the
 * parent form, so the test always uses what is typed right now.
 */
const M3UBackupUrls = ({ value, onChange, getTestValues, accountId }) => {
  const [results, setResults] = useState({});
  const [testing, setTesting] = useState(false);
  const [testedPrimary, setTestedPrimary] = useState('');

  const update = (next) => onChange(next);

  const setUrl = (index, url) => {
    const next = [...value];
    next[index] = url;
    update(next);
  };

  const testUrls = async () => {
    const { server_url, ...rest } = getTestValues();
    const urls = [server_url, ...cleanBackupUrls(value)]
      .map((url) => (url || '').trim())
      .filter(Boolean);
    if (urls.length === 0) return;

    setTesting(true);
    setTestedPrimary((server_url || '').trim());
    setResults(Object.fromEntries(urls.map((url) => [url, { pending: true }])));
    try {
      const response = await API.testM3UServerUrls({
        ...rest,
        urls,
        account_id: accountId || undefined,
      });
      const next = {};
      for (const result of response?.results || []) {
        next[result.url] = result;
      }
      setResults(next);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Input.Wrapper
      label="Backup URLs"
      description="Other addresses of the same provider. Used if the primary fails during refresh, and during playback a stream that fails or keeps buffering is tried on each server before switching to the next stream."
    >
      <Stack gap={6} mt={6}>
        {testedPrimary && results[testedPrimary] && (
          <Group gap="xs" wrap="nowrap">
            <Text
              size="xs"
              c="dimmed"
              style={{ flex: 1, minWidth: 0 }}
              truncate
            >
              Primary: {testedPrimary}
            </Text>
            <ResultBadge result={results[testedPrimary]} />
          </Group>
        )}
        {value.map((url, index) => (
          <Group key={index} gap={4} wrap="nowrap" align="center">
            <TextInput
              style={{ flex: 1, minWidth: 0 }}
              size="xs"
              placeholder={
                getTestValues().account_type === 'XC'
                  ? 'http://backup-host:port'
                  : 'http://backup-host:port/get.php?...'
              }
              aria-label={serverLabel(index + 1)}
              value={url}
              onChange={(event) => setUrl(index, event.currentTarget.value)}
              rightSection={<ResultBadge result={results[url.trim()]} />}
              rightSectionWidth={results[url.trim()] ? 110 : undefined}
            />
            <ActionIcon
              variant="subtle"
              size="sm"
              disabled={index === 0}
              onClick={() => update(moveItem(value, index, index - 1))}
              aria-label="Move up"
            >
              <ArrowUp size={14} />
            </ActionIcon>
            <ActionIcon
              variant="subtle"
              size="sm"
              disabled={index === value.length - 1}
              onClick={() => update(moveItem(value, index, index + 1))}
              aria-label="Move down"
            >
              <ArrowDown size={14} />
            </ActionIcon>
            <ActionIcon
              variant="subtle"
              size="sm"
              color="red.9"
              onClick={() => update(value.filter((_, i) => i !== index))}
              aria-label="Remove backup URL"
            >
              <SquareMinus size={16} />
            </ActionIcon>
          </Group>
        ))}
        <Group gap="xs">
          <Button
            size="xs"
            variant="light"
            leftSection={<Plus size={14} />}
            disabled={value.length >= MAX_BACKUP_SERVER_URLS}
            onClick={() => update([...value, ''])}
          >
            Add backup URL
          </Button>
          <Button
            size="xs"
            variant="default"
            loading={testing}
            onClick={testUrls}
          >
            Test URLs
          </Button>
        </Group>
      </Stack>
    </Input.Wrapper>
  );
};

export default M3UBackupUrls;
